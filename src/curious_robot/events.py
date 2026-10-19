"""Typed, public event contract shared by live execution and replay.

The models in this module deliberately contain presentation and evidence data,
not simulator internals.  Newton remains the physics authority; visual frames
are immutable display samples of that authority.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

SCHEMA_VERSION = "1.0.0"


class EventContractError(ValueError):
    """An event violated the public or ordering-independent event contract."""


class DiscoveryStage(StrEnum):
    OBSERVE = "observe"
    REASON = "reason"
    IDENTIFY_UNKNOWNS = "identify_unknowns"
    DESIGN_EXPERIMENT = "design_experiment"
    ACT = "act"
    MEASURE = "measure"
    SYSTEM_IDENTIFICATION = "system_identification"
    UPDATE_SIMULATION = "update_simulation"
    PREDICT = "predict"
    SELECT_ACTION = "select_action"
    EXECUTE = "execute"
    VERIFY = "verify"
    LEARN = "learn"


class MacroPhase(StrEnum):
    OBSERVE = "observe"
    DISCOVER = "discover"
    EXPERIMENT = "experiment"
    CALIBRATE = "calibrate"
    DECIDE = "decide"
    EXECUTE = "execute"


STAGE_TO_MACRO: dict[DiscoveryStage, MacroPhase] = {
    DiscoveryStage.OBSERVE: MacroPhase.OBSERVE,
    DiscoveryStage.REASON: MacroPhase.DISCOVER,
    DiscoveryStage.IDENTIFY_UNKNOWNS: MacroPhase.DISCOVER,
    DiscoveryStage.DESIGN_EXPERIMENT: MacroPhase.DISCOVER,
    DiscoveryStage.ACT: MacroPhase.EXPERIMENT,
    DiscoveryStage.MEASURE: MacroPhase.EXPERIMENT,
    DiscoveryStage.SYSTEM_IDENTIFICATION: MacroPhase.CALIBRATE,
    DiscoveryStage.UPDATE_SIMULATION: MacroPhase.CALIBRATE,
    DiscoveryStage.PREDICT: MacroPhase.DECIDE,
    DiscoveryStage.SELECT_ACTION: MacroPhase.DECIDE,
    DiscoveryStage.EXECUTE: MacroPhase.EXECUTE,
    DiscoveryStage.VERIFY: MacroPhase.EXECUTE,
    DiscoveryStage.LEARN: MacroPhase.EXECUTE,
}


class PublicModel(BaseModel):
    """Forward-compatible public model.

    Additional fields from the same schema major are retained by Pydantic and
    therefore survive a parse/serialize round trip.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


class Uncertainty(PublicModel):
    standard_deviation: float = Field(ge=0.0)
    interval95: tuple[float, float]
    physical_bounds: tuple[float, float]
    censored_bound: Literal["lower_open", "upper_open"] | None = None

    @model_validator(mode="after")
    def ordered_bounds(self) -> Uncertainty:
        if self.interval95[0] > self.interval95[1]:
            raise ValueError("interval95 must be ordered")
        if self.physical_bounds[0] > self.physical_bounds[1]:
            raise ValueError("physical_bounds must be ordered")
        return self


class BodyTransform(PublicModel):
    body_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    asset_node_id: str = Field(min_length=1)
    position_m: Vec3
    orientation_xyzw: Quat


class ContactVisual(PublicModel):
    position_m: Vec3
    normal_world: Vec3
    force_world_n: Vec3
    shape_a_label: str
    shape_b_label: str


class VectorVisual(PublicModel):
    vector_id: str
    kind: Literal["measured_force", "commanded_force", "target", "prediction", "observed"]
    origin_m: Vec3
    vector: Vec3
    frame: str
    units: str
    label: str | None = None


class TargetRegionVisual(PublicModel):
    center_m: Vec3
    radius_m: float = Field(gt=0.0)
    label: str = "target"


class CameraHint(PublicModel):
    preset: str
    target_m: Vec3 | None = None


class VisualFramePayload(PublicModel):
    frame_id: int = Field(ge=0)
    source_state_sequence: int = Field(ge=0)
    scene: Literal["truth", "belief"]
    world_instance_id: UUID
    scenario_id: str | None = None
    action_id: str | None = None
    belief_revision: int | None = Field(default=None, ge=0)
    body_manifest_id: str = Field(min_length=1)
    sim_time_s: float = Field(ge=0.0)
    presentation_time_s: float = Field(ge=0.0)
    bodies: tuple[BodyTransform, ...]
    contacts: tuple[ContactVisual, ...] = ()
    vectors: tuple[VectorVisual, ...] = ()
    target_region: TargetRegionVisual | None = None
    camera_hint: CameraHint | None = None
    contacts_truncated: int = Field(default=0, ge=0)
    vectors_truncated: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unique_bodies(self) -> VisualFramePayload:
        body_ids = [body.body_id for body in self.bodies]
        asset_ids = [body.asset_node_id for body in self.bodies]
        if len(body_ids) != len(set(body_ids)):
            raise ValueError("body_id values must be unique in a visual frame")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_node_id values must be unique in a visual frame")
        return self


class RunPayload(PublicModel):
    state: str
    reason: str | None = None
    configuration_commitment: str | None = None
    worker_pid: int | None = Field(default=None, gt=0)


class StageEnteredPayload(PublicModel):
    stage_instance_id: UUID
    iteration: int = Field(ge=0)
    prior_stage: DiscoveryStage | None = None


class StageCompletedPayload(PublicModel):
    stage_instance_id: UUID
    iteration: int = Field(ge=0)
    next_stage: DiscoveryStage | None = None
    outcome: Literal["completed", "failed", "skipped"]
    evidence_ids: tuple[str, ...] = ()
    duration_s: float = Field(ge=0.0)
    skipped_reason: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def outcome_has_reason(self) -> StageCompletedPayload:
        if self.outcome == "skipped" and not self.skipped_reason:
            raise ValueError("a skipped stage requires skipped_reason")
        if self.outcome == "failed" and not self.failure_reason:
            raise ValueError("a failed stage requires failure_reason")
        return self


class RationalePayload(PublicModel):
    summary: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    considered_option_ids: tuple[str, ...]
    selected_option_id: str


class ToolStartedPayload(PublicModel):
    tool_call_id: UUID
    tool_name: str
    arguments: dict[str, Any]


class ToolCompletedPayload(PublicModel):
    tool_call_id: UUID
    tool_name: str
    duration_s: float = Field(ge=0.0)
    result: dict[str, Any]


class ToolFailedPayload(PublicModel):
    tool_call_id: UUID
    tool_name: str
    duration_s: float = Field(ge=0.0)
    error: str


class MeasurementSampledPayload(PublicModel):
    evidence_id: str
    sensor_kind: str
    producer: str
    frame: str
    units: str
    values: dict[str, float | tuple[float, ...]]
    valid: bool


class MeasurementSummaryPayload(PublicModel):
    evidence_id: str
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    aggregate_values: dict[str, float | tuple[float, ...]]
    quality_flags: tuple[str, ...] = ()


class ParameterDistribution(PublicModel):
    mean: float
    uncertainty: Uncertainty
    units: str


class BeliefUpdatedPayload(PublicModel):
    revision: int = Field(ge=1)
    parameter_name: str
    prior: ParameterDistribution
    posterior: ParameterDistribution
    method: str
    evidence_ids: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    mismatch_notes: tuple[str, ...] = ()


class BeliefRebuiltPayload(PublicModel):
    belief_revision: int = Field(ge=0)
    world_instance_id: UUID
    body_manifest_id: str
    parameter_names: tuple[str, ...]


class PredictionPayload(PublicModel):
    action_id: str
    scenario_id: str
    final_position_m: Vec3
    target_error_m: float = Field(ge=0.0)
    success_probability: float = Field(ge=0.0, le=1.0)
    peak_force_n: float = Field(ge=0.0)
    tipping_margin: float
    feasible: bool
    reasons: tuple[str, ...] = ()


class ActionSelectedPayload(PublicModel):
    selected_action_id: str
    candidate_scores: dict[str, float]
    rationale_event_id: UUID


class ActionExecutedPayload(PublicModel):
    action_id: str
    controller_result: str
    observed_terminal_position_m: Vec3
    safety_event_ids: tuple[UUID, ...] = ()


class VerificationPayload(PublicModel):
    success: bool
    goal_predicates: dict[str, bool]
    prediction_residual_m: float = Field(ge=0.0)
    tolerance_m: float = Field(gt=0.0)


class LearningPayload(PublicModel):
    affected_parameters: tuple[str, ...]
    old_uncertainty: dict[str, float]
    new_uncertainty: dict[str, float]
    residual_evidence_ids: tuple[str, ...] = ()
    follow_up_decision: str


class SafetyPayload(PublicModel):
    condition: str
    measured_value: float
    limit: float
    units: str
    controller_response: str
    can_continue: bool


class EvaluationPayload(PublicModel):
    """Evaluator-only post-terminal data; never legal in the public event log."""

    comparison: dict[str, Any]


class ReexecutionPayload(PublicModel):
    environment_fingerprints: dict[str, str]
    tolerances: dict[str, float]
    maximum_errors: dict[str, float]
    rms_errors: dict[str, float]
    sample_count: int = Field(ge=0)
    passed: bool


class ArtifactPayload(PublicModel):
    artifact_id: UUID
    kind: str
    relative_path: str
    media_type: str
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("artifact path must be relative and cannot traverse parents")
        return value


class EventBase(PublicModel):
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(ge=1)
    wall_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sim_time_s: float | None = Field(default=None, ge=0.0)
    stage: DiscoveryStage
    frame_id: int | None = Field(default=None, ge=0)
    schema_version: str = SCHEMA_VERSION
    iteration_id: int = Field(ge=0)
    stage_instance_id: UUID

    @field_validator("wall_time")
    @classmethod
    def wall_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("wall_time must include an RFC 3339 timezone")
        return value

    @field_validator("schema_version")
    @classmethod
    def compatible_schema(cls, value: str) -> str:
        pieces = value.split(".")
        if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
            raise ValueError("schema_version must be SemVer")
        if int(pieces[0]) != int(SCHEMA_VERSION.split(".")[0]):
            raise ValueError(f"unsupported event schema major {pieces[0]}; upgrade required")
        return value


def _event_model(name: str, event_type: str, payload_type: type[PublicModel]) -> type[EventBase]:
    annotations = {"type": Literal[event_type], "payload": payload_type}
    return type(
        name,
        (EventBase,),
        {"__annotations__": annotations, "__module__": __name__, "type": event_type},
    )


RunStartedEvent = _event_model("RunStartedEvent", "run.started", RunPayload)
RunPausedEvent = _event_model("RunPausedEvent", "run.paused", RunPayload)
RunResumedEvent = _event_model("RunResumedEvent", "run.resumed", RunPayload)
RunCompletedEvent = _event_model("RunCompletedEvent", "run.completed", RunPayload)
RunFailedEvent = _event_model("RunFailedEvent", "run.failed", RunPayload)
RunCancelledEvent = _event_model("RunCancelledEvent", "run.cancelled", RunPayload)
RunAbandonedEvent = _event_model("RunAbandonedEvent", "run.abandoned", RunPayload)
StageEnteredEvent = _event_model("StageEnteredEvent", "stage.entered", StageEnteredPayload)
StageCompletedEvent = _event_model("StageCompletedEvent", "stage.completed", StageCompletedPayload)
RationalePublishedEvent = _event_model("RationalePublishedEvent", "rationale.published", RationalePayload)
ToolStartedEvent = _event_model("ToolStartedEvent", "tool.started", ToolStartedPayload)
ToolCompletedEvent = _event_model("ToolCompletedEvent", "tool.completed", ToolCompletedPayload)
ToolFailedEvent = _event_model("ToolFailedEvent", "tool.failed", ToolFailedPayload)
MeasurementSampledEvent = _event_model(
    "MeasurementSampledEvent", "measurement.sampled", MeasurementSampledPayload
)
MeasurementSummaryEvent = _event_model(
    "MeasurementSummaryEvent", "measurement.summary", MeasurementSummaryPayload
)
BeliefUpdatedEvent = _event_model("BeliefUpdatedEvent", "belief.updated", BeliefUpdatedPayload)
SimulationBeliefRebuiltEvent = _event_model(
    "SimulationBeliefRebuiltEvent", "simulation.belief_rebuilt", BeliefRebuiltPayload
)
SimulationVisualFrameEvent = _event_model(
    "SimulationVisualFrameEvent", "simulation.visual_frame", VisualFramePayload
)
PredictionCompletedEvent = _event_model("PredictionCompletedEvent", "prediction.completed", PredictionPayload)
ActionSelectedEvent = _event_model("ActionSelectedEvent", "action.selected", ActionSelectedPayload)
ActionExecutedEvent = _event_model("ActionExecutedEvent", "action.executed", ActionExecutedPayload)
VerificationCompletedEvent = _event_model(
    "VerificationCompletedEvent", "verification.completed", VerificationPayload
)
LearningCompletedEvent = _event_model("LearningCompletedEvent", "learning.completed", LearningPayload)
SafetyTriggeredEvent = _event_model("SafetyTriggeredEvent", "safety.triggered", SafetyPayload)
EvaluationRevealedEvent = _event_model("EvaluationRevealedEvent", "evaluation.revealed", EvaluationPayload)
ReexecutionCompletedEvent = _event_model(
    "ReexecutionCompletedEvent", "reexecution.completed", ReexecutionPayload
)
ArtifactCreatedEvent = _event_model("ArtifactCreatedEvent", "artifact.created", ArtifactPayload)

type AgentEvent = Annotated[
    RunStartedEvent
    | RunPausedEvent
    | RunResumedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent
    | RunAbandonedEvent
    | StageEnteredEvent
    | StageCompletedEvent
    | RationalePublishedEvent
    | ToolStartedEvent
    | ToolCompletedEvent
    | ToolFailedEvent
    | MeasurementSampledEvent
    | MeasurementSummaryEvent
    | BeliefUpdatedEvent
    | SimulationBeliefRebuiltEvent
    | SimulationVisualFrameEvent
    | PredictionCompletedEvent
    | ActionSelectedEvent
    | ActionExecutedEvent
    | VerificationCompletedEvent
    | LearningCompletedEvent
    | SafetyTriggeredEvent
    | EvaluationRevealedEvent
    | ReexecutionCompletedEvent
    | ArtifactCreatedEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER = TypeAdapter(AgentEvent)

TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled", "run.abandoned"})

# Exact keys rather than broad substrings avoid rejecting legitimate terms such
# as ``scene=truth`` and prose that explains the experiment's hidden premise.
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "ground_truth",
        "ground_truth_parameters",
        "hidden_parameters",
        "hidden_mass",
        "hidden_mass_kg",
        "true_mass",
        "true_mass_kg",
        "true_friction",
        "truth_mass_kg",
        "truth_mu_static",
        "truth_mu_dynamic",
        "surface_profile_id",
        "private_profile_id",
        "hidden_bounds",
        "newton_material_array",
        "newton_material_arrays",
    }
)


def validate_public_data(value: Any, *, forbidden_numeric_values: tuple[float, ...] = ()) -> None:
    """Recursively reject evaluator-only fields and optional sentinel values."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in FORBIDDEN_PUBLIC_KEYS:
                raise EventContractError(f"forbidden evaluator-only public field: {key}")
            validate_public_data(child, forbidden_numeric_values=forbidden_numeric_values)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            validate_public_data(child, forbidden_numeric_values=forbidden_numeric_values)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise EventContractError("public event contains a non-finite number")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and any(
            math.isclose(float(value), sentinel, rel_tol=0.0, abs_tol=0.0)
            for sentinel in forbidden_numeric_values
        )
    ):
        raise EventContractError("public event contains a forbidden evaluator sentinel value")


def validate_public_event(event: AgentEvent, *, forbidden_numeric_values: tuple[float, ...] = ()) -> None:
    if event.type == "evaluation.revealed":
        raise EventContractError("evaluation.revealed belongs in the evaluator channel, not the public log")
    if event.type == "simulation.visual_frame":
        if event.frame_id != event.payload.frame_id:
            raise EventContractError("visual event frame_id must match its payload frame_id")
        if event.sim_time_s != event.payload.sim_time_s:
            raise EventContractError("visual event sim_time_s must match its payload sim_time_s")
        if event.payload.source_state_sequence > event.sequence:
            raise EventContractError("visual frame cannot cite a future source state sequence")
    validate_public_data(event, forbidden_numeric_values=forbidden_numeric_values)


def parse_event(data: bytes | str) -> AgentEvent:
    """Parse one event and reject unknown/incompatible event types visibly."""

    return EVENT_ADAPTER.validate_json(data)


def serialize_event(event: AgentEvent) -> bytes:
    """Return the canonical compact JSON bytes (without the NDJSON newline)."""

    value = event.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


__all__ = [
    "AgentEvent",
    "ArtifactCreatedEvent",
    "BodyTransform",
    "DiscoveryStage",
    "EVENT_ADAPTER",
    "EventContractError",
    "MacroPhase",
    "RunCompletedEvent",
    "RunPayload",
    "RunStartedEvent",
    "SCHEMA_VERSION",
    "STAGE_TO_MACRO",
    "SimulationVisualFrameEvent",
    "TERMINAL_EVENT_TYPES",
    "VisualFramePayload",
    "parse_event",
    "serialize_event",
    "validate_public_data",
    "validate_public_event",
]
