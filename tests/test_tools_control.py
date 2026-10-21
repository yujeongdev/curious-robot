from types import SimpleNamespace

import pytest

from curious_robot.belief import initial_belief
from curious_robot.environment import WorldConfig
from curious_robot.protocol import load_default_protocol
from curious_robot.schemas import Pose
from curious_robot.tools import RobotTools


class _FailingComplianceReality:
    sim_time_s = 0.0

    def __init__(self) -> None:
        self.compliance_active = False
        self.release_count = 0

    def observe_object_pose(self):  # noqa: ANN201
        return SimpleNamespace(pose=Pose(position_m=(-0.12, 0.0, 0.025)))

    def enable_compliance(self) -> None:
        self.compliance_active = True
        raise RuntimeError("gain update failed")

    def release_compliance(self) -> None:
        self.compliance_active = False
        self.release_count += 1


def test_contact_probe_releases_compliance_when_activation_fails() -> None:
    reality = _FailingComplianceReality()
    config = WorldConfig(device="cpu", include_robot=False)
    tools = RobotTools(reality, initial_belief(), config)
    protocol = load_default_protocol().static_probe

    with pytest.raises(RuntimeError, match="gain update failed"):
        tools._sample_contact_push(
            start_x_m=-0.2,
            end_x_m=-0.1,
            y_m=0.0,
            z_m=0.04,
            increment_m=protocol.command_increment_m,
            substeps=protocol.physics_substeps_per_command,
            stop_after_breakaway=True,
            protocol=protocol,
            experiment_id="activation-failure",
        )

    assert reality.compliance_active is False
    assert reality.release_count == 1
