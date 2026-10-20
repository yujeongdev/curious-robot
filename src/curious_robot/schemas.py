"""Typed public contracts. No ground-truth field is exposed from this module."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParameterName(StrEnum):
    MASS = "mass_kg"
    STATIC_FRICTION = "mu_static"
    DYNAMIC_FRICTION = "mu_dynamic"
    COM_X = "com_x_m"
    COM_Y = "com_y_m"
    COM_Z = "com_z_m"


class Pose(FrozenModel):
    position_m: Vec3
    orientation_xyzw: Quat = (0.0, 0.0, 0.0, 1.0)


class JointState(FrozenModel):
    names: tuple[str, ...]
    position_rad: tuple[float, ...]
    velocity_rad_s: tuple[float, ...]
    timestamp_s: float


class RobotState(FrozenModel):
    joints: JointState
    end_effector_pose: Pose
    gripper_width_m: float


class ObjectPoseObservation(FrozenModel):
    pose: Pose
    linear_velocity_m_s: Vec3
    angular_velocity_rad_s: Vec3
    covariance_diagonal: tuple[float, float, float, float, float, float]
    timestamp_s: float


class SceneObservation(FrozenModel):
    object: ObjectPoseObservation
    target_center_m: Vec3
    target_radius_m: float
    table_height_m: float
    robot: RobotState


class WrenchObservation(FrozenModel):
    force_n: Vec3
    torque_nm: Vec3
    frame: Literal["world", "tool"]
    producer: str
    timestamp_s: float
    standard_deviation_n: float


class MotionSample(FrozenModel):
    timestamp_s: float
    position_m: Vec3
    velocity_m_s: Vec3
    measured_force_n: Vec3
    contact_valid: bool = False
    contact_pair: tuple[str, str] | None = None
    contact_position_m: Vec3 | None = None
    contact_force_producer: str | None = None
    experiment_id: str | None = None
    command_id: str | None = None
    experiment_phase: Literal["unassigned", "approach", "contact", "contact_lost"] = "unassigned"
    measured_normal_load_n: float | None = Field(default=None, ge=0.0)
    normal_load_standard_deviation_n: float | None = Field(default=None, ge=0.0)
    normal_load_producer: str | None = None

    @model_validator(mode="after")
    def validate_normal_load_measurement(self) -> MotionSample:
        fields = (
            self.measured_normal_load_n,
            self.normal_load_standard_deviation_n,
            self.normal_load_producer,
        )
        if any(value is not None for value in fields) and any(value is None for value in fields):
            raise ValueError("normal-load value, uncertainty, and producer must be supplied together")
        contact_fields = (self.contact_pair, self.contact_position_m, self.contact_force_producer)
        if self.contact_valid and any(value is None for value in contact_fields):
            raise ValueError("valid contact requires pair, position, and force producer")
        return self


class ExperimentEvidence(FrozenModel):
    evidence_id: str
    experiment: Literal["weigh", "static_friction", "dynamic_friction", "center_of_mass"]
    samples: tuple[MotionSample, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    validation: bool = False


class ParameterEstimate(FrozenModel):
    name: ParameterName
    mean: float
    std: float = Field(ge=0.0)
    interval95: tuple[float, float]
    physical_bounds: tuple[float, float]
    units: str
    status: Literal["prior", "identified", "bounded", "unidentifiable"]
    evidence_ids: tuple[str, ...] = ()
    method: str
    assumptions: tuple[str, ...] = ()
    mismatch_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_intervals(self) -> ParameterEstimate:
        if self.interval95[0] > self.interval95[1]:
            raise ValueError("interval95 must be ordered")
        if self.physical_bounds[0] > self.mean or self.mean > self.physical_bounds[1]:
            raise ValueError("mean outside physical bounds")
        return self


class PhysicalBelief(FrozenModel):
    revision: int = 0
    parameters: dict[ParameterName, ParameterEstimate]
    supporting_evidence_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = (
        "rigid cube with known geometry",
        "horizontal stationary table",
        "Coulomb friction is an effective model",
    )


class ExperimentCandidate(FrozenModel):
    candidate_id: str
    experiment: Literal["weigh", "probe_static", "probe_dynamic", "estimate_com"]
    targets: tuple[ParameterName, ...]
    expected_std_fraction: float = Field(gt=0.0, lt=1.0)
    duration_s: float = Field(gt=0.0)
    cost: float = Field(ge=0.0)
    risk: float = Field(ge=0.0)
    max_force_n: float = Field(gt=0.0)


class ExperimentScore(FrozenModel):
    candidate_id: str
    expected_information_gain: float
    relevance_weighted_gain: float
    cost_penalty: float
    risk_penalty: float
    feasible: bool
    rejection_reasons: tuple[str, ...] = ()
    utility: float


class ExperimentPlan(FrozenModel):
    selected: str | None
    scores: tuple[ExperimentScore, ...]
    rationale: str


class ActionCandidate(FrozenModel):
    action_id: str
    kind: Literal["push", "pick_place"]
    target_m: Vec3
    speed_m_s: float
    max_force_n: float
    push_height_m: float | None = None


class PredictedOutcome(FrozenModel):
    action_id: str
    final_position_m: Vec3
    target_error_m: float
    success_probability: float
    tipping_margin: float
    peak_force_n: float
    duration_s: float
    feasible: bool
    rationale: str
    scenario_id: str | None = None
    world_instance_id: UUID | None = None
    world_config_id: str | None = None
    experiment_protocol_id: str | None = None
    belief_revision: int | None = Field(default=None, ge=0)
    belief_snapshot_id: str | None = None


class ActionDecision(FrozenModel):
    selected_action_id: str
    outcomes: tuple[PredictedOutcome, ...]
    rationale: str


class ActionResult(FrozenModel):
    action_id: str
    observed_final_position_m: Vec3
    predicted_final_position_m: Vec3
    prediction_error_m: float
    success: bool


class ToolCallRecord(FrozenModel):
    sequence: int
    timestamp_s: float
    tool: str
    arguments: dict[str, Any]
    result_summary: dict[str, Any]


class EpisodeResult(FrozenModel):
    seed: int
    success: bool
    selected_action: str
    experiment_count: int
    final_belief: PhysicalBelief
    action_result: ActionResult
    output_directory: str
