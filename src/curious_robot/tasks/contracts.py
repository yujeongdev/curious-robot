"""Versioned task goals independent of robot and object construction."""

from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import Field, model_validator

from curious_robot.schemas import FrozenModel


class PlanarPose(FrozenModel):
    x_m: float
    y_m: float
    yaw_rad: float = 0.0

    @model_validator(mode="after")
    def validate_finite(self) -> PlanarPose:
        if not all(math.isfinite(value) for value in (self.x_m, self.y_m, self.yaw_rad)):
            raise ValueError("planar pose must be finite")
        return self


class PlanarGoal(FrozenModel):
    target: PlanarPose
    position_tolerance_m: float = Field(gt=0.0)
    yaw_tolerance_rad: float | None = Field(default=None, gt=0.0, le=math.pi)

    @property
    def requires_yaw(self) -> bool:
        return self.yaw_tolerance_rad is not None


class TaskDefinition(FrozenModel):
    """Operation-level rules reusable across object archetypes."""

    task_definition_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    objective: str = Field(min_length=1)
    permitted_action_families: tuple[str, ...] = Field(min_length=1)
    permitted_experiment_ids: tuple[str, ...] = Field(min_length=1)
    maximum_experiments: int = Field(ge=0, le=12)

    @model_validator(mode="after")
    def validate_unique_capabilities(self) -> TaskDefinition:
        if len(set(self.permitted_action_families)) != len(self.permitted_action_families):
            raise ValueError("permitted action families must be unique")
        if len(set(self.permitted_experiment_ids)) != len(self.permitted_experiment_ids):
            raise ValueError("permitted experiment IDs must be unique")
        return self


class ActionTemplate(FrozenModel):
    """Public, bounded controller parameters resolved into an action candidate."""

    action_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["push", "pick_place"]
    speed_m_s: float = Field(gt=0.0, le=0.15)
    max_force_n: float = Field(gt=0.0, le=15.0)
    push_height_m: float | None = Field(default=None, ge=0.0, le=0.05)
    lateral_contact_offset_m: float = Field(default=0.0, ge=-0.03, le=0.03)
    contact_surface_id: str | None = Field(default=None, min_length=1)


class PushProtocolOverrides(FrozenModel):
    """Task-specific physical-probe bounds layered over the global protocol."""

    maximum_travel_m: float | None = Field(default=None, gt=0.0)
    maximum_attempts: int | None = Field(default=None, gt=0, le=3)


class ExperimentProtocolOverrides(FrozenModel):
    static_probe: PushProtocolOverrides = PushProtocolOverrides()
    dynamic_probe: PushProtocolOverrides = PushProtocolOverrides()
    final_push: PushProtocolOverrides = PushProtocolOverrides()


class YawAlignmentPolicy(FrozenModel):
    """Bounded pre-translation side-push policy for planar pose tasks."""

    enabled: bool = False
    clockwise_surface_id: str | None = None
    counterclockwise_surface_id: str | None = None
    maximum_attempts: int = Field(default=0, ge=0, le=4)
    correction_travel_m: float = Field(default=0.0, ge=0.0, le=0.05)
    speed_m_s: float = Field(default=0.03, gt=0.0, le=0.10)
    push_height_m: float = Field(default=0.012, ge=0.0, le=0.05)
    stop_tolerance_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    position_stop_tolerance_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    adaptive_translation_surface: bool = False

    @model_validator(mode="after")
    def validate_enabled_policy(self) -> YawAlignmentPolicy:
        if self.enabled and (
            not self.clockwise_surface_id
            or not self.counterclockwise_surface_id
            or self.maximum_attempts == 0
            or self.correction_travel_m == 0.0
        ):
            raise ValueError("enabled yaw alignment requires surfaces, attempts, and travel")
        return self


class TaskPreset(FrozenModel):
    """Immutable binding of a goal, object archetype, and public initial state."""

    preset_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*-v[1-9][0-9]*$")
    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    task_definition_id: str
    task_definition_version: str
    object_archetype_id: str
    object_archetype_version: str
    initial_pose: PlanarPose
    initial_position_jitter_m: tuple[float, float, float, float]
    initial_yaw_jitter_rad: tuple[float, float] = (0.0, 0.0)
    default_mass_range_kg: tuple[float, float]
    experiment_contact_surface_id: str = Field(min_length=1)
    experiment_protocol_overrides: ExperimentProtocolOverrides = ExperimentProtocolOverrides()
    yaw_alignment: YawAlignmentPolicy = YawAlignmentPolicy()
    goal: PlanarGoal
    action_candidates: tuple[ActionTemplate, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_action_ids(self) -> TaskPreset:
        ids = tuple(action.action_id for action in self.action_candidates)
        if len(set(ids)) != len(ids):
            raise ValueError("task action candidate IDs must be unique")
        x_low, x_high, y_low, y_high = self.initial_position_jitter_m
        if not all(
            math.isfinite(value)
            for value in (
                *self.initial_position_jitter_m,
                *self.initial_yaw_jitter_rad,
                *self.default_mass_range_kg,
            )
        ):
            raise ValueError("task randomization bounds must be finite")
        if x_low > x_high or y_low > y_high:
            raise ValueError("initial-position jitter bounds must be ordered")
        yaw_low, yaw_high = self.initial_yaw_jitter_rad
        if yaw_low > yaw_high or yaw_low < -math.pi / 4.0 or yaw_high > math.pi / 4.0:
            raise ValueError("initial-yaw jitter must be ordered within [-pi/4, pi/4]")
        if (
            self.default_mass_range_kg[0] < 0.02
            or self.default_mass_range_kg[0] > self.default_mass_range_kg[1]
            or self.default_mass_range_kg[1] > 2.0
        ):
            raise ValueError("default mass bounds must satisfy 0.02 <= min <= max <= 2.0")
        return self

    @property
    def preset_hash(self) -> str:
        payload = self.model_dump_json(exclude_none=False)
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class GoalEvaluation(FrozenModel):
    success: bool
    position_error_m: float = Field(ge=0.0)
    yaw_error_rad: float | None = Field(default=None, ge=0.0, le=math.pi)
    predicates: dict[str, bool]


def evaluate_planar_goal(goal: PlanarGoal, observed: PlanarPose) -> GoalEvaluation:
    """Evaluate public task predicates without access to hidden physical truth."""

    position_error = math.hypot(observed.x_m - goal.target.x_m, observed.y_m - goal.target.y_m)
    predicates = {"position_within_tolerance": position_error <= goal.position_tolerance_m}
    yaw_error = None
    if goal.requires_yaw:
        yaw_error = wrapped_yaw_error(observed.yaw_rad, goal.target.yaw_rad)
        predicates["yaw_within_tolerance"] = yaw_error <= (goal.yaw_tolerance_rad or 0.0)
    return GoalEvaluation(
        success=all(predicates.values()),
        position_error_m=position_error,
        yaw_error_rad=yaw_error,
        predicates=predicates,
    )


def yaw_from_quaternion_xyzw(quaternion: tuple[float, float, float, float]) -> float:
    """Return wrapped world-frame yaw for an ``(x, y, z, w)`` quaternion."""

    x, y, z, w = quaternion
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def wrapped_yaw_error(observed_yaw_rad: float, target_yaw_rad: float) -> float:
    """Return the shortest absolute angular distance in ``[0, π]``."""

    return abs((observed_yaw_rad - target_yaw_rad + math.pi) % (2.0 * math.pi) - math.pi)


def signed_yaw_error(observed_yaw_rad: float, target_yaw_rad: float) -> float:
    """Return the shortest signed correction from observed yaw to target yaw."""

    return (target_yaw_rad - observed_yaw_rad + math.pi) % (2.0 * math.pi) - math.pi
