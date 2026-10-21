"""Hardware-replaceable command and observation boundary for the reality plant."""

from __future__ import annotations

from typing import Any, Protocol

from .control import PushExecutionResult
from .schemas import MotionSample, ObjectPoseObservation, Pose, WrenchObservation
from .trajectory import CartesianMotionResult


class RobotRealityPort(Protocol):
    """The only capabilities experiments may use from simulated or real reality."""

    sim_time_s: float

    def observe_object_pose(self) -> ObjectPoseObservation: ...
    def observe_tool_pose(self) -> Pose: ...
    def tool_controller_pose(self) -> Pose: ...
    def get_tool_wrench(self) -> WrenchObservation: ...
    def joint_state_arrays(self): ...  # noqa: ANN201
    def command_joint_targets(self, targets: dict[int, float], duration_s: float = 0.5) -> None: ...
    def command_cartesian(
        self, target_m: tuple[float, float, float], duration_s: float
    ) -> CartesianMotionResult: ...
    def set_cartesian_target(self, target_m: tuple[float, float, float]) -> None: ...
    def set_gripper(self, width_m: float) -> None: ...
    def enable_compliance(self) -> None: ...
    def release_compliance(self) -> None: ...
    def hold_current_joint_positions(self, duration_s: float) -> None: ...
    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        """Command force only when the adapter has validated measured-feedback control."""
        ...

    def step(self) -> MotionSample: ...
    def execute_push(self, target_m, max_force_n, protocol) -> PushExecutionResult: ...  # noqa: ANN001


class RobotRealityAdapter:
    """Narrow runtime capability object exposed to agent tools.

    Python is not a security sandbox, but explicit forwarding prevents normal
    agent code from receiving Newton model/state objects or hidden physical
    parameters. A hardware backend can implement the same port directly.
    """

    __slots__ = ("__plant",)

    def __init__(self, plant: Any) -> None:
        self.__plant = plant

    @property
    def sim_time_s(self) -> float:
        return float(self.__plant.sim_time_s)

    def observe_object_pose(self) -> ObjectPoseObservation:
        return self.__plant.observe_object_pose()

    def observe_tool_pose(self) -> Pose:
        return self.__plant.observe_tool_pose()

    def tool_controller_pose(self) -> Pose:
        return self.__plant.tool_controller_pose()

    def get_tool_wrench(self) -> WrenchObservation:
        return self.__plant.get_tool_wrench()

    def joint_state_arrays(self):  # noqa: ANN201
        return self.__plant.joint_state_arrays()

    def command_joint_targets(self, targets: dict[int, float], duration_s: float = 0.5) -> None:
        self.__plant.command_joint_targets(targets, duration_s)

    def command_cartesian(
        self, target_m: tuple[float, float, float], duration_s: float
    ) -> CartesianMotionResult:
        return self.__plant.command_cartesian(target_m, duration_s)

    def set_cartesian_target(self, target_m: tuple[float, float, float]) -> None:
        self.__plant.set_cartesian_target(target_m)

    def set_gripper(self, width_m: float) -> None:
        self.__plant.set_gripper(width_m)

    def enable_compliance(self) -> None:
        self.__plant.enable_compliance()

    def release_compliance(self) -> None:
        self.__plant.release_compliance()

    def hold_current_joint_positions(self, duration_s: float) -> None:
        self.__plant.hold_current_joint_positions(duration_s)

    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        self.__plant.set_desired_force(force_n)

    def step(self) -> MotionSample:
        return self.__plant.step()

    def execute_push(self, target_m, max_force_n, protocol) -> PushExecutionResult:  # noqa: ANN001
        return self.__plant.execute_push(target_m, max_force_n, protocol=protocol)
