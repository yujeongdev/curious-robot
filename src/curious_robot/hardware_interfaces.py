"""Typed interfaces shared by Franka hardware adapters and transport plugins."""

from __future__ import annotations

import math
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from curious_robot.hardware_motion import FrankaPushMotionPlan, HardwareToolPose


class _HardwareModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class HardwareObservation(_HardwareModel):
    episode_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]
    frame_id: str = Field(min_length=1)
    captured_monotonic_s: float = Field(ge=0.0)
    covariance_trace_m2: float = Field(ge=0.0)
    source_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_orientation(self) -> HardwareObservation:
        norm = math.sqrt(sum(component**2 for component in self.orientation_xyzw))
        if not math.isclose(norm, 1.0, abs_tol=1e-6):
            raise ValueError("hardware object orientation must be a unit quaternion")
        return self


class HardwareEpisode(_HardwareModel):
    episode_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    initial_observation: HardwareObservation


class HardwarePushResult(_HardwareModel):
    motion_plan_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    controller_outcome: Literal["completed", "safety_stopped", "travel_exhausted"]
    peak_contact_force_n: float = Field(ge=0.0)
    maximum_measured_speed_m_s: float = Field(ge=0.0)
    maximum_measured_acceleration_m_s2: float = Field(ge=0.0)
    maximum_measured_angular_speed_rad_s: float = Field(ge=0.0)
    maximum_measured_angular_acceleration_rad_s2: float = Field(ge=0.0)
    maximum_measured_orientation_error_rad: float = Field(ge=0.0)
    elapsed_s: float = Field(ge=0.0)
    safety_tripped: bool
    watchdog_token: str = Field(min_length=1)
    deadline_enforced: bool


class HardwareMotionPreflight(_HardwareModel):
    plan_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feasible: bool
    checks: tuple[str, ...]
    errors: tuple[str, ...] = ()


class HardwarePerception(Protocol):
    def start_episode(self, invocation_id: str, timeout_s: float) -> HardwareEpisode: ...
    def observe_object_position(self, timeout_s: float) -> HardwareObservation: ...
    def end_episode(self, episode_id: str) -> None: ...


class FrankaHardwareTransport(Protocol):
    @property
    def backend_name(self) -> str: ...

    def execute_bounded_push(
        self,
        *,
        motion_plan: FrankaPushMotionPlan,
        maximum_force_n: float,
        maximum_speed_m_s: float,
        command_timeout_s: float,
    ) -> HardwarePushResult: ...

    def read_tool_pose(self, timeout_s: float) -> HardwareToolPose: ...
    def validate_motion_plan(
        self, motion_plan: FrankaPushMotionPlan, timeout_s: float
    ) -> HardwareMotionPreflight: ...
    def arm_watchdog(self, command_timeout_s: float) -> str: ...
    def disarm_watchdog(self, watchdog_token: str) -> None: ...
    def safe_stop(self) -> None: ...
    def close(self) -> None: ...
