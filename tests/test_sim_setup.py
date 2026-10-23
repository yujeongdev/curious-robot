from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from curious_robot import sim_setup
from curious_robot.curo_cli import main
from curious_robot.schemas import ObjectPoseObservation, Pose
from curious_robot.tasks import get_task_preset


class FakeArray:
    def __init__(self, values):  # noqa: ANN001
        self.values = np.asarray(values, dtype=float)

    def numpy(self):  # noqa: ANN202
        return self.values

    def assign(self, values):  # noqa: ANN001, ANN202
        self.values = np.asarray(values, dtype=float)


class FakeWorld:
    def __init__(self, config):  # noqa: ANN001
        self.config = config
        self.model = SimpleNamespace(device=config.device)
        self.world_instance_id = "public-prior-world"
        self.world_config_id = config.config_id
        self.state_0 = SimpleNamespace(
            body_q=FakeArray([[*config.initial_position_m, 0, 0, 0, 1]]),
            body_qd=FakeArray([[0] * 6]),
            joint_q=FakeArray([0.1]),
            joint_qd=FakeArray([0]),
        )
        self.control = SimpleNamespace(joint_target_q=FakeArray([0]))
        self.sim_time_s = 0.0
        self.close = Mock()

    def step(self):  # noqa: ANN202
        self.sim_time_s += self.config.dt_s

    def observe_object_pose(self):  # noqa: ANN202
        return ObjectPoseObservation(
            timestamp_s=self.sim_time_s,
            pose=Pose(position_m=self.config.initial_position_m),
            linear_velocity_m_s=(0.0, 0.0, 0.0),
            angular_velocity_rad_s=(0.0, 0.0, 0.0),
            covariance_diagonal=(0.0,) * 6,
        )


def test_setup_uses_selected_task_and_only_public_priors(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    built = []

    def build(belief, config):  # noqa: ANN001, ANN202
        world = FakeWorld(config)
        built.append((belief, config, world))
        return world

    monkeypatch.setenv("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED", "private-do-not-read")
    report = sim_setup.setup_simulation(
        task_id="push-t-bar-v1",
        output_directory=tmp_path / "setup",
        device="cuda:0",
        steps=3,
        world_factory=build,
    )
    belief, config, world = built[0]
    task = get_task_preset("push-t-bar-v1")
    assert config.task_id == task.preset_id
    assert config.object_kind == "t_bar"
    assert config.target_radius_m == task.goal.position_tolerance_m
    assert config.target_yaw_tolerance_rad == task.goal.yaw_tolerance_rad
    assert belief.revision == 0
    assert report["status"] == "passed"
    assert report["purpose"] == "prior_only_setup_smoke"
    assert report["parameter_source"] == "public_initial_belief"
    assert report["checks"]["completed_steps"] == 3
    assert report["runtime"]["actual_device"] == "cuda:0"
    assert "private-do-not-read" not in json.dumps(report)
    assert world.control.joint_target_q.numpy() == pytest.approx([0.1])
    world.close.assert_called_once()
    assert json.loads(Path(report["report_path"]).read_text()) == report


def test_setup_runtime_failure_is_persisted_and_not_replaced(tmp_path) -> None:
    builder = Mock(side_effect=RuntimeError("requested GPU is unavailable"))
    report = sim_setup.setup_simulation(
        task_id="push-cube-v1",
        output_directory=tmp_path / "setup",
        device="cuda:0",
        steps=1,
        world_factory=builder,
    )
    assert report["status"] == "failed"
    assert report["checks"]["completed_steps"] == 0
    assert report["error"]["message"] == "requested GPU is unavailable"
    assert Path(report["report_path"]).exists()
    builder.assert_called_once()
    with pytest.raises(FileExistsError):
        sim_setup.setup_simulation(
            task_id="push-cube-v1", output_directory=tmp_path / "setup", world_factory=builder
        )
    builder.assert_called_once()
    assert json.loads(Path(report["report_path"]).read_text()) == report


def test_setup_rejects_nan_state_and_closes_world(tmp_path) -> None:
    worlds = []

    def build(_belief, config):  # noqa: ANN001, ANN202
        world = FakeWorld(config)
        world.state_0.joint_qd = FakeArray([float("nan")])
        worlds.append(world)
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path / "setup", steps=1, world_factory=build
    )
    assert report["status"] == "failed"
    assert "NaN or Inf" in report["error"]["message"]
    assert report["checks"]["finite_state"] is False
    worlds[0].close.assert_called_once()


@pytest.mark.parametrize("steps", [0, -1, 2401, True])
def test_invalid_step_budget_fails_before_creating_output(tmp_path, steps) -> None:  # noqa: ANN001
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="steps must be"):
        sim_setup.setup_simulation(task_id="push-cube-v1", output_directory=output, steps=steps)
    assert not output.exists()


def test_unknown_task_fails_before_creating_output(tmp_path) -> None:  # noqa: ANN001
    output = tmp_path / "not-created"
    with pytest.raises(KeyError, match="unsupported task"):
        sim_setup.setup_simulation(task_id="bowling-not-registered", output_directory=output)
    assert not output.exists()


def test_setup_cli_failure_is_one_json_result(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setattr(sim_setup, "_build_prior_world", Mock(side_effect=RuntimeError("no GPU")))
    with pytest.raises(SystemExit) as error:
        main(["sim", "setup", "--task", "push-cube-v1", "--output", str(tmp_path / "setup")])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["purpose"] == "prior_only_setup_smoke"
    assert report["error"]["message"] == "no GPU"
    assert report["error"]["code"] == "runtime_error"


@pytest.mark.parametrize(
    ("requested", "actual"),
    [("cuda:0", "cuda:1"), ("cuda:1", "cuda:0"), ("cpu", "cuda:0"), ("cuda:0", "cpu")],
)
def test_setup_rejects_exact_device_mismatch(tmp_path, requested, actual) -> None:
    worlds = []

    def build(_belief, config):
        world = FakeWorld(config)
        world.model.device = actual
        worlds.append(world)
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, device=requested, steps=1, world_factory=build
    )
    assert report["status"] == "failed"
    assert report["runtime"]["actual_device"] == actual
    assert report["checks"]["world_initialized"] is True
    assert report["checks"]["completed_steps"] == 0
    assert report["error"]["message"] == f"requested {requested}, but world was built on {actual}"
    worlds[0].close.assert_called_once()


def test_setup_normalizes_runtime_device_alias(tmp_path, monkeypatch) -> None:
    import sys

    resolver = Mock(return_value="cuda:1")
    monkeypatch.setitem(sys.modules, "warp", SimpleNamespace(get_device=resolver))

    def build(_belief, config):
        world = FakeWorld(config)
        world.model.device = "cuda:1"
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, device="cuda", steps=1, world_factory=build
    )
    assert report["status"] == "passed"
    assert report["runtime"]["resolved_device"] == "cuda:1"
    resolver.assert_called_once_with("cuda")


def test_observation_failure_preserves_passed_state_checks(tmp_path) -> None:
    worlds = []

    def build(_belief, config):
        world = FakeWorld(config)
        world.observe_object_pose = Mock(side_effect=RuntimeError("pose producer unavailable"))
        worlds.append(world)
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, steps=2, world_factory=build
    )
    assert report["status"] == "failed"
    assert report["checks"]["finite_state"] is True
    assert report["checks"]["completed_steps"] == 2
    assert report["checks"]["observation_returned"] is False
    assert report["error"]["message"] == "pose producer unavailable"
    worlds[0].close.assert_called_once()


def test_concurrent_setup_requests_cannot_replace_owned_report(tmp_path) -> None:
    initialized = Event()
    finish = Event()
    competing_factory = Mock(side_effect=AssertionError("competing request allocated a world"))

    def build(_belief, config):
        initialized.set()
        if not finish.wait(timeout=5):
            raise TimeoutError("test did not release the setup world")
        return FakeWorld(config)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            sim_setup.setup_simulation,
            task_id="push-cube-v1",
            output_directory=tmp_path,
            steps=1,
            world_factory=build,
        )
        try:
            assert initialized.wait(timeout=5)
            active_report = json.loads((tmp_path / "setup_report.json").read_text())
            assert active_report["status"] == "failed"
            assert active_report["error"]["code"] == "setup_incomplete"
            with pytest.raises(FileExistsError):
                sim_setup.setup_simulation(
                    task_id="push-t-bar-v1",
                    output_directory=tmp_path,
                    world_factory=competing_factory,
                )
        finally:
            finish.set()
        report = first.result(timeout=5)
    assert report["status"] == "passed"
    assert report["task_id"] == "push-cube-v1"
    assert json.loads((tmp_path / "setup_report.json").read_text()) == report
    competing_factory.assert_not_called()


def test_primary_and_cleanup_failures_are_both_preserved(tmp_path) -> None:
    def build(_belief, config):
        world = FakeWorld(config)
        world.step = Mock(side_effect=RuntimeError("step failed"))
        world.close.side_effect = RuntimeError("cleanup failed")
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, steps=1, world_factory=build
    )
    assert report["status"] == "failed"
    assert report["error"]["message"] == "step failed"
    assert report["cleanup_error"]["message"] == "cleanup failed"


def test_reduced_scene_can_step_without_robot_joint_arrays(tmp_path) -> None:
    def build(_belief, config):
        world = FakeWorld(config)
        world.state_0.joint_q = None
        world.state_0.joint_qd = None
        world.control.joint_target_q = None
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1",
        output_directory=tmp_path,
        steps=1,
        include_robot=False,
        world_factory=build,
    )
    assert report["status"] == "passed"
    assert report["configuration"]["include_robot"] is False
    assert report["checks"]["finite_state"] is True


def test_interrupted_setup_keeps_a_readable_failure_report(tmp_path) -> None:
    worlds = []

    def build(_belief, config):
        world = FakeWorld(config)
        world.step = Mock(side_effect=KeyboardInterrupt)
        worlds.append(world)
        return world

    with pytest.raises(KeyboardInterrupt):
        sim_setup.setup_simulation(
            task_id="push-cube-v1", output_directory=tmp_path, steps=1, world_factory=build
        )
    report = json.loads((tmp_path / "setup_report.json").read_text())
    assert report["status"] == "failed"
    assert report["error"]["code"] == "setup_incomplete"
    worlds[0].close.assert_called_once()


def test_nonfinite_simulation_time_is_a_validation_failure(tmp_path) -> None:
    def build(_belief, config):
        world = FakeWorld(config)
        world.sim_time_s = float("nan")
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, steps=1, world_factory=build
    )
    assert report["status"] == "failed"
    assert report["error"]["code"] == "validation_failed"
    assert "simulation time" in report["error"]["message"]
    assert json.loads((tmp_path / "setup_report.json").read_text()) == report


def test_report_serialization_failure_retains_safe_runtime_evidence(tmp_path) -> None:
    def build(_belief, config):
        world = FakeWorld(config)
        world.world_instance_id = float("nan")
        return world

    report = sim_setup.setup_simulation(
        task_id="push-cube-v1", output_directory=tmp_path, steps=1, world_factory=build
    )
    assert report["status"] == "failed"
    assert report["error"]["code"] == "runtime_error"
    assert report["checks"]["completed_steps"] == 1
    assert json.loads((tmp_path / "setup_report.json").read_text()) == report
