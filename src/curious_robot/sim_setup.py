"""Build and step a task's public prior model without sampling hidden Reality."""

from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import re
from collections.abc import Callable
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .environment import BeliefEnvironment
    from .schemas import PhysicalBelief
    from .tasks import ResolvedTask
    from .world_config import WorldConfig

DEFAULT_SETUP_STEPS = 24
MAX_SETUP_STEPS = 2400
type WorldFactory = Callable[[PhysicalBelief, WorldConfig], BeliefEnvironment]


def _runtime_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("newton", "warp-lang", "mujoco", "mujoco-warp"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _build_prior_world(belief: PhysicalBelief, config: WorldConfig) -> BeliefEnvironment:
    from .environment import BeliefEnvironment

    return BeliefEnvironment.from_belief(belief, config, scenario_id="prior-only-setup")


def _task_configuration(task: ResolvedTask, *, device: str, include_robot: bool) -> WorldConfig:
    from .world_config import WorldConfig

    preset = task.preset
    support_height = -min(part.center_object_m[2] - part.half_extents_m[2] for part in task.archetype.parts)
    return WorldConfig(
        task_id=preset.preset_id,
        device=device,
        initial_position_m=(preset.initial_pose.x_m, preset.initial_pose.y_m, support_height),
        initial_yaw_rad=preset.initial_pose.yaw_rad,
        target_position_m=(preset.goal.target.x_m, preset.goal.target.y_m, support_height),
        target_radius_m=preset.goal.position_tolerance_m,
        target_yaw_rad=preset.goal.target.yaw_rad,
        target_yaw_tolerance_rad=preset.goal.yaw_tolerance_rad,
        include_robot=include_robot,
        seed=0,
    )


def _initial_report(
    task: ResolvedTask,
    config: WorldConfig,
    belief: PhysicalBelief,
    *,
    steps: int,
    report_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "curo-sim-setup-v1",
        "status": "failed",
        "purpose": "prior_only_setup_smoke",
        "task_id": task.preset.preset_id,
        "task_version": task.preset.version,
        "task_preset_hash": task.preset.preset_hash,
        "object_archetype_id": task.archetype.archetype_id,
        "geometry_hash": task.archetype.geometry_hash,
        "parameter_source": "public_initial_belief",
        "initial_belief": belief.model_dump(mode="json"),
        "configuration": asdict(config),
        "runtime": {"versions": _runtime_versions(), "requested_device": config.device},
        "checks": {
            "world_initialized": False,
            "finite_state": False,
            "observation_returned": False,
            "completed_steps": 0,
            "requested_steps": steps,
        },
        "report_path": str(report_path),
        "limitations": [
            "Public initial priors only; no hidden Reality parameters are sampled or read.",
            "Finite stepping does not validate contact physics or identify material properties.",
            "This report does not authorize deployment or demonstrate task success.",
        ],
    }


def _device_alias(device: str) -> str:
    if device == "cpu":
        return device
    if match := re.fullmatch(r"cuda:(\d+)", device):
        return f"cuda:{int(match[1])}"
    # Resolve runtime aliases such as 'cuda' only after the world initialized.
    import warp as wp

    return str(wp.get_device(device))


def _smoke_rollout(world: BeliefEnvironment, *, steps: int, report: dict[str, Any]) -> None:
    import numpy as np

    actual_device = str(world.model.device)
    resolved_device = _device_alias(report["runtime"]["requested_device"])
    report["runtime"].update(actual_device=actual_device, resolved_device=resolved_device)
    report["world"] = {
        "authority": "twin",
        "world_instance_id": world.world_instance_id,
        "world_config_id": world.world_config_id,
    }
    report["checks"]["world_initialized"] = True
    if actual_device != resolved_device:
        raise RuntimeError(f"requested {resolved_device}, but world was built on {actual_device}")
    # Hold the public initial robot posture rather than command a zero target.
    target_q = world.control.joint_target_q
    initial_q = world.state_0.joint_q
    if report["configuration"]["include_robot"] and (target_q is None or initial_q is None):
        raise RuntimeError("robot joint state or control targets are unavailable")
    if target_q is not None and initial_q is not None:
        target_q.assign(initial_q.numpy())
    for _ in range(steps):
        world.step()
        state = world.state_0
        if state.body_q is None or state.body_qd is None:
            raise RuntimeError("simulation body state is unavailable")
        if not all(
            np.isfinite(array.numpy()).all()
            for array in (state.body_q, state.body_qd, state.joint_q, state.joint_qd)
            if array is not None
        ):
            raise RuntimeError("simulation state contains NaN or Inf")
        report["checks"]["completed_steps"] += 1
    report["checks"]["finite_state"] = True
    report["simulation_time_s"] = world.sim_time_s
    observation = world.observe_object_pose()
    observed_values = chain(
        observation.pose.position_m,
        observation.pose.orientation_xyzw,
        observation.linear_velocity_m_s,
        observation.angular_velocity_rad_s,
        observation.covariance_diagonal,
        (observation.timestamp_s,),
    )
    if not all(math.isfinite(value) for value in observed_values):
        raise RuntimeError("simulation observation contains NaN or Inf")
    report["observation"] = observation.model_dump(mode="json")
    report["checks"]["observation_returned"] = True
    report["status"] = "passed"


def setup_simulation(
    *,
    task_id: str,
    output_directory: str | Path,
    device: str = "cuda:0",
    steps: int = DEFAULT_SETUP_STEPS,
    include_robot: bool = True,
    world_factory: WorldFactory | None = None,
) -> dict[str, Any]:
    """Persist a real smoke report, reserving its path before allocating a world.

    The callable factory is the test seam; normal callers use the Newton prior
    builder. Runtime and cleanup failures remain in the report, while invalid
    inputs and competing writers fail before creating a world.
    """
    from .belief import initial_belief_for_object
    from .tasks import resolve_task

    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_SETUP_STEPS:
        raise ValueError(f"steps must be an integer in [1, {MAX_SETUP_STEPS}]")
    task = resolve_task(task_id)
    config = _task_configuration(task, device=device, include_robot=include_robot)
    belief = initial_belief_for_object(task.preset.object_archetype_id, task.preset.object_archetype_version)
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "setup_report.json"
    with report_path.open("x", encoding="utf-8") as report_file:
        report = _initial_report(task, config, belief, steps=steps, report_path=report_path)
        world = None
        try:
            world = (world_factory or _build_prior_world)(belief, config)
            _smoke_rollout(world, steps=steps, report=report)
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            if world is not None:
                try:
                    world.close()
                except Exception as error:
                    report["status"] = "failed"
                    report["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
        serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        report_file.write(serialized)
    # Match the persisted JSON representation, including tuples serialized as lists.
    return cast(dict[str, Any], json.loads(serialized))
