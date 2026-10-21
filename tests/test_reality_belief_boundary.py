from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from curious_robot.belief import initial_belief
from curious_robot.environment import NewtonWorld, WorldConfig
from curious_robot.ports import RobotRealityAdapter
from curious_robot.protocol import load_default_protocol
from curious_robot.tools import RobotTools


def test_robot_actions_cannot_apply_a_force_directly_to_cube() -> None:
    assert tuple(inspect.signature(NewtonWorld.step).parameters) == ("self",)
    agent_sources = [
        Path("src/curious_robot/tools.py"),
        Path("src/curious_robot/planning.py"),
        Path("src/curious_robot/sysid.py"),
    ]
    joined = "\n".join(path.read_text() for path in agent_sources)
    assert "cube_controller_state" not in joined
    assert "body_f" not in joined
    assert "._parameters" not in joined


def test_runtime_reality_capability_hides_privileged_newton_state() -> None:
    privileged_names = ("_parameters", "state_0", "model", "cube_controller_state", "solver")
    adapter_names = set(dir(RobotRealityAdapter))
    assert all(name not in adapter_names for name in privileged_names)

    class PrivilegedPlant:
        _parameters = object()

    with pytest.raises(TypeError, match="narrow RobotRealityPort"):
        RobotTools(PrivilegedPlant(), initial_belief(), WorldConfig())  # type: ignore[arg-type]


def test_physical_experiment_protocol_is_versioned_and_not_episode_truth() -> None:
    protocol = load_default_protocol()
    assert protocol.schema_version == "1.1.0"
    assert protocol.static_probe.maximum_horizontal_force_n == 15.0
    assert protocol.final_push.maximum_attempts >= 1
    serialized = protocol.model_dump_json()
    for forbidden in ("mass_kg", "mu_static", "mu_dynamic", "truth"):
        assert forbidden not in serialized
