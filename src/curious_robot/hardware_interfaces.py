"""Typed interfaces shared by Franka hardware adapters and transport plugins."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from curious_robot.protocol import PhysicalExperimentProtocol
from curious_robot.schemas import ActionCandidate


class _HardwareModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class HardwareObservation(_HardwareModel):
    episode_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    position_m: tuple[float, float, float]
    frame_id: str = Field(min_length=1)
    captured_monotonic_s: float = Field(ge=0.0)
    covariance_trace_m2: float = Field(ge=0.0)
    source_id: str = Field(min_length=1)


class HardwareEpisode(_HardwareModel):
    episode_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    initial_observation: HardwareObservation


class HardwarePushResult(_HardwareModel):
    controller_outcome: Literal["completed", "safety_stopped", "travel_exhausted"]
    peak_contact_force_n: float = Field(ge=0.0)
    maximum_measured_speed_m_s: float = Field(ge=0.0)
    elapsed_s: float = Field(ge=0.0)
    safety_tripped: bool
    watchdog_token: str = Field(min_length=1)
    deadline_enforced: bool


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
        action: ActionCandidate,
        protocol: PhysicalExperimentProtocol,
        maximum_force_n: float,
        maximum_speed_m_s: float,
        command_timeout_s: float,
    ) -> HardwarePushResult: ...

    def arm_watchdog(self, command_timeout_s: float) -> str: ...
    def disarm_watchdog(self, watchdog_token: str) -> None: ...
    def safe_stop(self) -> None: ...
    def close(self) -> None: ...
