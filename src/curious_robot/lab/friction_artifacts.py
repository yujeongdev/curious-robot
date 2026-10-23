"""Typed payloads and artifact transformations for the friction capability."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from curious_robot.belief import initial_belief_for_object, update_belief
from curious_robot.objects import get_object_archetype
from curious_robot.planning import candidate_actions
from curious_robot.protocol import (
    PhysicalExperimentProtocol,
    load_default_protocol,
    protocol_sha256,
)
from curious_robot.schemas import ActionCandidate, ExperimentEvidence, ParameterName
from curious_robot.sysid import estimate_dynamic_friction, estimate_static_friction
from curious_robot.tasks import get_task_preset

from .artifact_store import LaboratoryArtifactStores
from .contracts import (
    ArtifactEnvelope,
    ArtifactRef,
    LabArtifactKind,
    PatchOperation,
    WorldSpec,
    WorldSpecPatch,
)


class CapabilityExecutionError(RuntimeError):
    """A port or transformation failed a required capability operation."""


class _FrictionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class FrictionTaskSpec(_FrictionModel):
    kind: Literal["friction-aware-push"]
    frame_id: Literal["simulation_world"]
    task_id: str = Field(default="push-cube-v1", min_length=1)
    task_version: str = "1.0.0"
    object_id: str = Field(min_length=1)
    object_archetype_version: str = "1.0.0"
    geometry_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    protocol: PhysicalExperimentProtocol
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_position_m: tuple[float, float, float]
    target_radius_m: float = Field(gt=0.0)
    target_yaw_rad: float = 0.0
    yaw_tolerance_rad: float | None = Field(default=None, gt=0.0, le=math.pi)

    @model_validator(mode="after")
    def validate_protocol_identity(self) -> FrictionTaskSpec:
        if protocol_sha256(self.protocol) != self.protocol_sha256:
            raise ValueError("protocol_sha256 does not match the protocol snapshot")
        preset = get_task_preset(self.task_id)
        if self.object_id != preset.object_archetype_id:
            raise ValueError("task object does not match its allowlisted public preset")
        if preset.goal.requires_yaw and (self.yaw_tolerance_rad is None or self.geometry_hash is None):
            raise ValueError("pose task requires public geometry identity and yaw tolerance")
        if preset.goal.requires_yaw and (
            self.task_version != preset.version
            or self.object_archetype_version != preset.object_archetype_version
            or self.target_position_m[:2] != (preset.goal.target.x_m, preset.goal.target.y_m)
            or self.target_radius_m != preset.goal.position_tolerance_m
            or self.target_yaw_rad != preset.goal.target.yaw_rad
            or self.yaw_tolerance_rad != preset.goal.yaw_tolerance_rad
        ):
            raise ValueError("pose task does not match its allowlisted public preset")
        return self


class PublicBoxPart(_FrictionModel):
    part_id: str = Field(min_length=1)
    center_object_m: tuple[float, float, float]
    half_extents_m: tuple[float, float, float]


class PublicGeometrySpec(_FrictionModel):
    object_archetype_id: str = Field(default="cube", min_length=1)
    object_archetype_version: str = "1.0.0"
    geometry_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    parts: tuple[PublicBoxPart, ...] = ()
    cube_edge_m: float | None = Field(default=0.05, gt=0.0)
    initial_position_m: tuple[float, float, float]

    @model_validator(mode="after")
    def validate_geometry_identity(self) -> PublicGeometrySpec:
        archetype = get_object_archetype(
            self.object_archetype_id,
            self.object_archetype_version,
        )
        if self.object_archetype_id != "cube" and (
            self.geometry_hash is None or len(self.parts) != len(archetype.parts)
        ):
            raise ValueError("compound public geometry requires a hash and every declared part")
        if self.object_archetype_id != "cube":
            expected_parts = tuple(
                PublicBoxPart(
                    part_id=part.part_id,
                    center_object_m=part.center_object_m,
                    half_extents_m=part.half_extents_m,
                )
                for part in archetype.parts
            )
            if self.geometry_hash != archetype.geometry_hash or self.parts != expected_parts:
                raise ValueError("T-bar geometry does not match its allowlisted archetype")
        return self


class PublicNumericalConfig(_FrictionModel):
    dt_s: float = Field(default=1.0 / 240.0, gt=0.0)
    include_robot: bool = True
    initial_position_m: tuple[float, float, float]
    initial_yaw_rad: float = 0.0
    maximum_cartesian_speed_m_s: float = Field(default=0.15, gt=0.0)
    maximum_tool_force_n: float = Field(default=15.0, gt=0.0)
    observation_noise_std: float = Field(default=0.0002, ge=0.0)
    prediction_residual_tolerance_m: float = Field(default=0.03, gt=0.0)
    seed: int = Field(default=7, ge=0)
    target_position_m: tuple[float, float, float]
    target_radius_m: float = Field(gt=0.0)


class _ExperimentMetadata(_FrictionModel):
    producer: Literal[
        "Newton SensorContact Franka end-effector ↔ cube",
        "Newton SensorContact Franka end-effector ↔ t-bar",
    ]
    protocol_schema_version: str = Field(min_length=1)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_correlation_id: str = Field(min_length=1)
    raw_sample_count: int = Field(ge=0)
    accepted_contact_sample_count: int = Field(ge=0)
    rejected_noncontact_sample_count: int = Field(ge=0)
    contact_coverage_fraction: float = Field(ge=0.0, le=1.0)
    minimum_contact_coverage_fraction: float = Field(ge=0.0, le=1.0)
    minimum_valid_contact_samples: int = Field(gt=0)
    maximum_yaw_excursion_rad: float = Field(default=math.pi, ge=0.0, le=math.pi)
    observed_yaw_excursion_rad: float = Field(default=0.0, ge=0.0, le=math.pi)


class StaticFrictionMetadata(_ExperimentMetadata):
    controller: Literal["Franka IK minimum-jerk joint-target contact ramp"]
    motion_threshold_m_s: float = Field(gt=0.0)
    position_increment_m: float = Field(gt=0.0)
    force_model_discrepancy_fraction: float = Field(ge=0.0, le=1.0)


class DynamicFrictionMetadata(_ExperimentMetadata):
    controller: Literal["Franka IK minimum-jerk approach plus maintained-contact slide"]
    target_velocity_m_s: float = Field(gt=0.0)


class TrajectoryDatasetPayload(_FrictionModel):
    task: FrictionTaskSpec
    public_geometry: PublicGeometrySpec
    numerical_config: PublicNumericalConfig
    experiments: tuple[ExperimentEvidence, ...]
    world_instance_id: str = Field(min_length=1)
    runtime_process_id: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_public_evidence(self) -> TrajectoryDatasetPayload:
        experiments = tuple(item.experiment for item in self.experiments)
        if experiments != ("static_friction", "dynamic_friction"):
            raise ValueError("trajectory dataset requires static then dynamic friction evidence")
        for item in self.experiments:
            metadata_model = (
                StaticFrictionMetadata if item.experiment == "static_friction" else DynamicFrictionMetadata
            )
            metadata = metadata_model.model_validate(item.metadata)
            if metadata.experiment_correlation_id != item.evidence_id:
                raise ValueError("experiment correlation ID must equal the evidence ID")
            if (
                metadata.protocol_sha256 != self.task.protocol_sha256
                or metadata.protocol_schema_version != self.task.protocol.schema_version
            ):
                raise ValueError("experiment protocol identity must match the task WorldSpec")
            allowed_flags = {
                "excessive_yaw_motion",
                "insufficient_contact_coverage",
                "insufficient_native_contact_samples",
            }
            if item.experiment == "static_friction":
                allowed_flags.add("right_censored_no_breakaway")
            unexpected_flags = set(item.quality_flags) - allowed_flags
            if unexpected_flags:
                raise ValueError(f"experiment quality flags are not public: {sorted(unexpected_flags)}")
        return self


class DeploymentAttempt(_FrictionModel):
    controller_outcome: Literal["completed", "safety_stopped", "travel_exhausted"]
    target_error_m: float = Field(ge=0.0)
    yaw_error_rad: float | None = Field(default=None, ge=0.0, le=math.pi)


class RealityDeploymentPayload(_FrictionModel):
    action_id: str = Field(min_length=1)
    attempt_count: int = Field(gt=0, le=3)
    attempts: tuple[DeploymentAttempt, ...]
    controller_outcome: Literal["completed", "safety_stopped", "travel_exhausted"]
    request_id: str | None = Field(default=None, min_length=1)
    invocation_id: str | None = Field(default=None, min_length=1)
    attempt_index: int | None = Field(default=None, gt=0, le=3)
    fresh_episode: bool
    frame_id: str = Field(default="simulation_world", min_length=1)
    object_id: str = Field(default="cube", min_length=1)
    task_id: str = Field(default="push-cube-v1", min_length=1)
    task_version: str = Field(default="1.0.0", min_length=1)
    object_archetype_version: str = Field(default="1.0.0", min_length=1)
    geometry_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    motion_plan_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    action_graph_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    deployment_bundle_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    maximum_measured_speed_m_s: float | None = Field(default=None, ge=0.0)
    maximum_measured_acceleration_m_s2: float | None = Field(default=None, ge=0.0)
    maximum_measured_angular_speed_rad_s: float | None = Field(default=None, ge=0.0)
    maximum_measured_angular_acceleration_rad_s2: float | None = Field(
        default=None,
        ge=0.0,
    )
    maximum_measured_orientation_error_rad: float | None = Field(default=None, ge=0.0)
    motion_elapsed_s: float | None = Field(default=None, ge=0.0)
    observed_final_position_m: tuple[float, float, float]
    observed_final_yaw_rad: float = 0.0
    peak_contact_force_n: float = Field(ge=0.0)
    prediction_target_error_m: float = Field(ge=0.0)
    runtime_process_id: int = Field(gt=0)
    safety_tripped: bool
    target_position_m: tuple[float, float, float]
    target_radius_m: float = Field(gt=0.0)
    target_yaw_rad: float = 0.0
    yaw_tolerance_rad: float | None = Field(default=None, gt=0.0, le=math.pi)
    terminal: Literal[True]
    world_instance_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attempts(self) -> RealityDeploymentPayload:
        if (self.motion_plan_id is None) != (self.calibration_id is None):
            raise ValueError("hardware plan and calibration IDs must be supplied together")
        if (self.action_graph_id is not None or self.deployment_bundle_id is not None) and (
            self.motion_plan_id is None
        ):
            raise ValueError("hardware action graph and bundle require plan and calibration IDs")
        if self.deployment_bundle_id is not None and self.action_graph_id is None:
            raise ValueError("hardware deployment bundle requires an action graph ID")
        motion_measurements = (
            self.maximum_measured_speed_m_s,
            self.maximum_measured_acceleration_m_s2,
            self.maximum_measured_angular_speed_rad_s,
            self.maximum_measured_angular_acceleration_rad_s2,
            self.maximum_measured_orientation_error_rad,
            self.motion_elapsed_s,
        )
        if self.motion_plan_id is None and any(value is not None for value in motion_measurements):
            raise ValueError("hardware motion measurements require a motion plan")
        if self.motion_plan_id is not None and any(value is None for value in motion_measurements):
            raise ValueError("hardware motion measurements must be complete")
        if self.attempt_count != len(self.attempts):
            raise ValueError("attempt_count must equal the number of attempt records")
        if self.controller_outcome != self.attempts[-1].controller_outcome:
            raise ValueError("controller_outcome must match the final attempt")
        if self.object_id == "t-bar" and (
            self.task_id != "push-t-bar-v1" or self.geometry_hash is None or self.yaw_tolerance_rad is None
        ):
            raise ValueError("T-bar deployment requires task, geometry, and yaw identities")
        return self


class FrictionArtifactBuilder:
    """Pure public-evidence transformations plus writes to the coordinator CAS."""

    def __init__(self, stores: LaboratoryArtifactStores) -> None:
        self.stores = stores

    def estimate_posterior(self, dataset_ref: ArtifactRef) -> ArtifactRef:
        dataset = self.stores.agent.get(dataset_ref)
        payload = TrajectoryDatasetPayload.model_validate(dataset.payload)
        static, dynamic = payload.experiments
        belief = _initial_belief_for_task(payload.task)
        static_estimate = estimate_static_friction(
            static,
            belief.parameters[ParameterName.MASS],
        )
        if static_estimate.status == "unidentifiable":
            raise CapabilityExecutionError("static friction posterior is unidentifiable")
        belief = update_belief(belief, static_estimate)
        dynamic_estimate = estimate_dynamic_friction(
            dynamic,
            belief.parameters[ParameterName.MASS],
            belief.parameters[ParameterName.STATIC_FRICTION],
        )
        if dynamic_estimate.status == "unidentifiable":
            raise CapabilityExecutionError("dynamic friction posterior is unidentifiable")
        belief = update_belief(belief, dynamic_estimate)
        return self.stores.agent.put(
            ArtifactEnvelope(
                kind=LabArtifactKind.PARAMETER_POSTERIOR.value,
                payload={"physical_belief": belief.model_dump(mode="json")},
                provenance=(dataset_ref,),
                validation_conditions=(
                    "static and dynamic friction estimates pass physical bounds",
                    "posterior is derived only from trajectory observations",
                ),
            )
        )

    def build_world_spec(
        self,
        dataset_ref: ArtifactRef,
        posterior_ref: ArtifactRef,
    ) -> tuple[WorldSpec, ArtifactRef]:
        dataset = TrajectoryDatasetPayload.model_validate(self.stores.agent.get(dataset_ref).payload)
        posterior = _object_payload(
            self.stores.agent.get(posterior_ref).payload,
            LabArtifactKind.PARAMETER_POSTERIOR.value,
        )
        initial = _initial_belief_for_task(dataset.task)
        base = WorldSpec.create(
            operation_schema="friction-aware-push-v1",
            document={
                "task": dataset.task.model_dump(mode="json"),
                "public_geometry": dataset.public_geometry.model_dump(mode="json"),
                "numerical_config": dataset.numerical_config.model_dump(mode="json"),
                "model_parameters": {"physical_belief": initial.model_dump(mode="json")},
            },
        )
        patch = WorldSpecPatch(
            base_spec_id=base.spec_id,
            operations=(
                PatchOperation(
                    "set",
                    ("model_parameters", "physical_belief"),
                    posterior["physical_belief"],
                ),
            ),
            evidence=(posterior_ref,),
        )
        patched = patch.apply(base, resolver=self.stores.agent)
        patch_ref = self.stores.agent.put(
            ArtifactEnvelope(
                kind=LabArtifactKind.WORLD_SPEC_PATCH.value,
                payload={"base_spec_id": patch.base_spec_id, "patch_id": patch.patch_id},
                provenance=(posterior_ref,),
                validation_conditions=(
                    "patch base matches the current WorldSpec",
                    "only model_parameters are modified",
                ),
            )
        )
        world_spec = WorldSpec.create(
            operation_schema=patched.operation_schema,
            document=patched.document,
            sources=(patch_ref,),
            applied_patch_ids=patched.applied_patch_ids,
            schema_version=patched.schema_version,
        )
        return world_spec, patch_ref

    def distill_policy(self, validation_ref: ArtifactRef) -> ArtifactRef:
        validation = _object_payload(
            self.stores.agent.get(validation_ref).payload,
            LabArtifactKind.TWIN_VALIDATION_REPORT.value,
        )
        action = ActionCandidate.model_validate(validation["selected_action"])
        protocol = PhysicalExperimentProtocol.model_validate(validation["protocol"])
        protocol_id = protocol_sha256(protocol)
        if validation["protocol_sha256"] != protocol_id:
            raise CapabilityExecutionError("Twin validation protocol identity is inconsistent")
        max_attempts = int(validation["max_attempts"])
        if not 1 <= max_attempts <= 3:
            raise CapabilityExecutionError("Twin validation produced an invalid recovery bound")
        return self.stores.agent.put(
            ArtifactEnvelope(
                kind=LabArtifactKind.POLICY_ARTIFACT.value,
                payload={
                    "action": action.model_dump(mode="json"),
                    "task_id": validation.get("task_id", "push-cube-v1"),
                    "task_version": validation.get("task_version", "1.0.0"),
                    "object_id": validation.get("object_id", "cube"),
                    "object_archetype_version": validation.get(
                        "object_archetype_version", "1.0.0"
                    ),
                    "geometry_hash": validation.get("geometry_hash"),
                    "goal_target_m": validation["goal_target_m"],
                    "goal_target_radius_m": validation["goal_target_radius_m"],
                    "goal_target_yaw_rad": validation.get("goal_target_yaw_rad", 0.0),
                    "goal_yaw_tolerance_rad": validation.get("goal_yaw_tolerance_rad"),
                    "max_attempts": max_attempts,
                    "protocol": protocol.model_dump(mode="json"),
                    "protocol_sha256": protocol_id,
                },
                provenance=(validation_ref,),
                validation_conditions=(
                    "bounded push and recovery attempts reach the target in the independent Twin",
                    "force and Cartesian speed remain within configured bounds",
                ),
            )
        )


def default_world_spec_seed(
    *,
    initial_position_m: tuple[float, float, float],
    target_position_m: tuple[float, float, float],
    target_radius_m: float,
    world_config: dict[str, Any],
    protocol: PhysicalExperimentProtocol | None = None,
    task_id: str = "push-cube-v1",
) -> dict[str, Any]:
    """Build the public, task-oriented WorldSpec sections emitted by Reality."""

    protocol = protocol or load_default_protocol()
    preset = get_task_preset(task_id)
    archetype = get_object_archetype(
        preset.object_archetype_id,
        preset.object_archetype_version,
    )
    task = FrictionTaskSpec(
        kind="friction-aware-push",
        frame_id="simulation_world",
        task_id=preset.preset_id,
        task_version=preset.version,
        object_id=preset.object_archetype_id,
        object_archetype_version=preset.object_archetype_version,
        geometry_hash=archetype.geometry_hash,
        protocol=protocol,
        protocol_sha256=protocol_sha256(protocol),
        target_position_m=target_position_m,
        target_radius_m=target_radius_m,
        target_yaw_rad=preset.goal.target.yaw_rad,
        yaw_tolerance_rad=preset.goal.yaw_tolerance_rad,
    )
    geometry = PublicGeometrySpec(
        object_archetype_id=archetype.archetype_id,
        object_archetype_version=archetype.version,
        geometry_hash=archetype.geometry_hash,
        parts=tuple(
            PublicBoxPart(
                part_id=part.part_id,
                center_object_m=part.center_object_m,
                half_extents_m=part.half_extents_m,
            )
            for part in archetype.parts
        ),
        cube_edge_m=(
            archetype.parts[0].half_extents_m[0] * 2.0
            if archetype.archetype_id == "cube"
            else None
        ),
        initial_position_m=initial_position_m,
    )
    numerical = PublicNumericalConfig.model_validate(
        {
            **world_config,
            "initial_position_m": initial_position_m,
            "target_position_m": target_position_m,
            "target_radius_m": target_radius_m,
        }
    )
    return {
        "task": task.model_dump(mode="json"),
        "public_geometry": geometry.model_dump(mode="json"),
        "numerical_config": numerical.model_dump(mode="json"),
    }


def select_push_policy(target_m: tuple[float, float, float]) -> ActionCandidate:
    """Return the only currently validated manipulation family for this slice."""

    return next(action for action in candidate_actions(target_m) if action.kind == "push")


def _initial_belief_for_task(task: FrictionTaskSpec):
    return initial_belief_for_object(
        task.object_id,
        task.object_archetype_version,
    )


def candidate_push_policies(task: FrictionTaskSpec) -> tuple[ActionCandidate, ...]:
    """Return independent Newton rollout candidates admitted by the task."""

    return tuple(
        action
        for action in candidate_actions(task.target_position_m, task_id=task.task_id)
        if action.kind == "push"
    )


def _object_payload(payload: Any, kind: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CapabilityExecutionError(f"{kind} payload must be an object")
    return payload
