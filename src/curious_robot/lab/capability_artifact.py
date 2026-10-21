"""Content-addressed capability contracts and trusted distillation."""

from __future__ import annotations

import json
import math
from dataclasses import fields
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from curious_robot.protocol import PhysicalExperimentProtocol, protocol_sha256
from curious_robot.schemas import ActionCandidate, PhysicalBelief

from .artifact_store import LaboratoryArtifactStores, ScopedArtifactStore
from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
)
from .friction_artifacts import RealityDeploymentPayload, TrajectoryDatasetPayload
from .friction_capability import FrictionCapabilityResult


class _SkillModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ApplicabilityEnvelope(_SkillModel):
    operation_schema: Literal["friction-aware-push-v1"]
    required_operations: tuple[Literal[LabOperation.DEPLOY_CAPABILITY], ...]
    supported_action_kind: Literal["push"]
    validated_target_radius_m: float = Field(gt=0.0)
    maximum_planar_travel_m: float = Field(gt=0.0)
    validated_initial_position_m: tuple[float, float, float]
    initial_position_tolerance_m: float = Field(ge=0.0)
    validated_target_m: tuple[float, float, float]
    workspace_min_m: tuple[float, float, float]
    workspace_max_m: tuple[float, float, float]

    @model_validator(mode="after")
    def validate_workspace(self) -> ApplicabilityEnvelope:
        if any(low > high for low, high in zip(self.workspace_min_m, self.workspace_max_m, strict=True)):
            raise ValueError("workspace bounds must be ordered")
        for point in (self.validated_initial_position_m, self.validated_target_m):
            if not _inside_workspace(point, self):
                raise ValueError("validated points must lie inside the workspace")
        if self.required_operations != (LabOperation.DEPLOY_CAPABILITY,):
            raise ValueError("capability requires exactly deploy_capability")
        return self


class SafetyGuardrail(_SkillModel):
    maximum_force_n: float = Field(gt=0.0, le=15.0)
    maximum_speed_m_s: float = Field(gt=0.0, le=0.15)
    maximum_attempts: int = Field(gt=0, le=3)
    stop_on_safety_trip: Literal[True] = True
    require_completed_controller: Literal[True] = True


class RecoveryPolicy(_SkillModel):
    maximum_attempts: int = Field(gt=0, le=3)
    success_condition: Literal["target_error_lte_radius"]
    on_safety_trip: Literal["return_to_agent"]
    on_attempts_exhausted: Literal["return_to_agent"]


class CapabilityArtifactPayload(_SkillModel):
    name: Literal["friction-aware-push"]
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_deployment_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_trajectory_dataset_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attestation_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evaluation_report_commitment: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    action_template: ActionCandidate
    protocol: PhysicalExperimentProtocol
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applicability: ApplicabilityEnvelope
    safety: SafetyGuardrail
    recovery: RecoveryPolicy

    @model_validator(mode="after")
    def validate_consistency(self) -> CapabilityArtifactPayload:
        if protocol_sha256(self.protocol) != self.protocol_sha256:
            raise ValueError("capability protocol hash does not match its snapshot")
        if self.safety.maximum_attempts != self.recovery.maximum_attempts:
            raise ValueError("safety and recovery attempt bounds must match")
        if self.action_template.max_force_n > self.safety.maximum_force_n:
            raise ValueError("action force exceeds the independent safety guardrail")
        if self.action_template.speed_m_s > self.safety.maximum_speed_m_s:
            raise ValueError("action speed exceeds the independent safety guardrail")
        if self.action_template.target_m != self.applicability.validated_target_m:
            raise ValueError("action target must equal the validated target")
        return self


class CapabilityAttestationPayload(_SkillModel):
    decision: Literal["approved"]
    deployment_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evaluation_report_commitment: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    terminal: Literal[True]


class CapabilityInvocation(_SkillModel):
    attempt_index: int = Field(gt=0, le=3)
    capability_artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    current_position_m: tuple[float, float, float]
    invocation_id: str = Field(min_length=1)
    policy_artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    requested_target_m: tuple[float, float, float]
    target_radius_m: float = Field(gt=0.0)


def validate_capability_invocation(
    payload: CapabilityArtifactPayload,
    capability_ref: ArtifactRef,
    arguments: object,
) -> CapabilityInvocation:
    invocation = CapabilityInvocation.model_validate(arguments)
    if (
        invocation.capability_artifact_id != capability_ref.artifact_id
        or invocation.policy_artifact_id != payload.policy_artifact_id
        or math.dist(
            invocation.current_position_m,
            payload.applicability.validated_initial_position_m,
        )
        > payload.applicability.initial_position_tolerance_m
        or invocation.requested_target_m != payload.applicability.validated_target_m
        or invocation.target_radius_m != payload.applicability.validated_target_radius_m
        or invocation.attempt_index > payload.recovery.maximum_attempts
    ):
        raise ValueError("capability invocation is outside its validated contract")
    return invocation


def distill_friction_capability(
    stores: LaboratoryArtifactStores,
    result: FrictionCapabilityResult,
) -> ArtifactRef:
    """Declassify terminal success into a reusable agent-visible capability."""

    if (
        result.evaluation_report.kind != LabArtifactKind.EVALUATION_REPORT.value
        or result.evaluation_report.visibility is not ArtifactVisibility.EVALUATOR
        or result.evaluation_report.outcome is not ArtifactOutcome.PASS
    ):
        raise ValueError("distillation requires an evaluator PASS evaluation_report")
    evaluation = stores.evaluator.get(result.evaluation_report)
    if evaluation.provenance != (result.reality_deployment,):
        raise ValueError("evaluation report does not attest the source deployment")
    evaluation_payload = evaluation.payload
    if not isinstance(evaluation_payload, dict):
        raise ValueError("evaluation report payload must be an object")
    if evaluation_payload.get("terminal") is not True or evaluation_payload.get("success") is not True:
        raise ValueError("only a successful terminal evaluation can be distilled")

    lineage = (
        (result.trajectory_dataset, result.capability_gap),
        (result.parameter_posterior, result.trajectory_dataset),
        (result.world_spec_patch, result.parameter_posterior),
        (result.twin_build, result.world_spec_patch),
        (result.twin_validation_report, result.twin_build),
        (result.policy_artifact, result.twin_validation_report),
        (result.reality_deployment, result.policy_artifact),
    )
    for child, parent in lineage:
        envelope = stores.agent.get(child)
        if envelope.provenance != (parent,):
            raise ValueError(f"mixed or invalid run lineage at {child.kind}")
    gap = stores.agent.get(result.capability_gap).payload
    if not isinstance(gap, dict) or (
        gap.get("capability") != "friction-aware-push"
        or gap.get("missing_operations") != []
        or gap.get("missing_evidence_kinds") != []
    ):
        raise ValueError("capability inventory gap is not closed")

    dataset = TrajectoryDatasetPayload.model_validate(
        stores.agent.get(result.trajectory_dataset).payload
    )
    posterior_envelope = stores.agent.get(result.parameter_posterior)
    if not isinstance(posterior_envelope.payload, dict):
        raise ValueError("posterior payload must be an object")
    PhysicalBelief.model_validate(posterior_envelope.payload["physical_belief"])
    policy_envelope = stores.agent.get(result.policy_artifact)
    if not isinstance(policy_envelope.payload, dict):
        raise ValueError("policy payload must be an object")
    deployment_envelope = stores.agent.get(result.reality_deployment)
    deployment = RealityDeploymentPayload.model_validate(deployment_envelope.payload)

    action = ActionCandidate.model_validate(policy_envelope.payload["action"])
    protocol = PhysicalExperimentProtocol.model_validate(policy_envelope.payload["protocol"])
    attempt_bound = int(policy_envelope.payload["max_attempts"])
    initial = dataset.public_geometry.initial_position_m
    target = dataset.task.target_position_m
    initial_tolerance = 3.0 * dataset.numerical_config.observation_noise_std + 1e-6
    if (
        action.target_m != target
        or tuple(policy_envelope.payload["goal_target_m"]) != target
        or float(policy_envelope.payload["goal_target_radius_m"]) != dataset.task.target_radius_m
        or protocol != dataset.task.protocol
        or deployment.action_id != action.action_id
        or deployment.target_position_m != target
        or deployment.target_radius_m != dataset.task.target_radius_m
    ):
        raise ValueError("policy, deployment, and dataset semantics do not match")
    workspace_min = tuple(
        min(a - initial_tolerance, b) for a, b in zip(initial, target, strict=True)
    )
    workspace_max = tuple(
        max(a + initial_tolerance, b) for a, b in zip(initial, target, strict=True)
    )
    travel = math.dist(initial[:2], target[:2]) + initial_tolerance
    declassifier = ScopedArtifactStore(
        stores.agent,
        read_kinds=frozenset(),
        write_kinds=frozenset(
            {
                LabArtifactKind.CAPABILITY_ATTESTATION.value,
                LabArtifactKind.CAPABILITY_ARTIFACT.value,
            }
        ),
    )
    attestation = declassifier.put(
        ArtifactEnvelope(
            kind=LabArtifactKind.CAPABILITY_ATTESTATION.value,
            payload=CapabilityAttestationPayload(
                decision="approved",
                deployment_id=result.reality_deployment.artifact_id,
                evaluation_report_commitment=result.evaluation_report.artifact_id,
                terminal=True,
            ).model_dump(mode="json"),
            provenance=(result.reality_deployment,),
            validation_conditions=(
                "trusted coordinator verified a successful terminal evaluation",
            ),
        )
    )
    payload = CapabilityArtifactPayload(
        name="friction-aware-push",
        policy_artifact_id=result.policy_artifact.artifact_id,
        source_deployment_id=result.reality_deployment.artifact_id,
        source_trajectory_dataset_id=result.trajectory_dataset.artifact_id,
        attestation_id=attestation.artifact_id,
        evaluation_report_commitment=result.evaluation_report.artifact_id,
        action_template=action,
        protocol=protocol,
        protocol_sha256=protocol_sha256(protocol),
        applicability=ApplicabilityEnvelope(
            operation_schema="friction-aware-push-v1",
            required_operations=(LabOperation.DEPLOY_CAPABILITY,),
            supported_action_kind="push",
            validated_target_radius_m=dataset.task.target_radius_m,
            maximum_planar_travel_m=travel,
            validated_initial_position_m=initial,
            initial_position_tolerance_m=initial_tolerance,
            validated_target_m=target,
            workspace_min_m=workspace_min,
            workspace_max_m=workspace_max,
        ),
        safety=SafetyGuardrail(
            maximum_force_n=action.max_force_n,
            maximum_speed_m_s=action.speed_m_s,
            maximum_attempts=attempt_bound,
        ),
        recovery=RecoveryPolicy(
            maximum_attempts=attempt_bound,
            success_condition="target_error_lte_radius",
            on_safety_trip="return_to_agent",
            on_attempts_exhausted="return_to_agent",
        ),
    )
    return declassifier.put(
        ArtifactEnvelope(
            kind=LabArtifactKind.CAPABILITY_ARTIFACT.value,
            payload=payload.model_dump(mode="json"),
            provenance=(
                result.policy_artifact,
                result.reality_deployment,
                attestation,
                result.trajectory_dataset,
            ),
            validation_conditions=(
                "trusted terminal evaluator approved the source deployment",
                "applicability, safety, and recovery contracts are explicit",
                "nominal execution requires no LLM decision",
            ),
        )
    )


def distill_from_manifest(run_directory: str | Path) -> ArtifactRef:
    root = Path(run_directory)
    manifest = json.loads((root / "friction_capability_manifest.json").read_text())
    stores = LaboratoryArtifactStores(root)
    result = _result_from_manifest(manifest)
    reference = distill_friction_capability(stores, result)
    (root / "capability_artifact.json").write_text(
        json.dumps(
            {
                "artifact_id": reference.artifact_id,
                "kind": reference.kind,
                "schema_version": "1.0.0",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return reference


def _result_from_manifest(manifest: dict) -> FrictionCapabilityResult:
    artifact_ids = manifest["artifacts"]
    values = {}
    for field in fields(FrictionCapabilityResult):
        visibility = (
            ArtifactVisibility.EVALUATOR
            if field.name == "evaluation_report"
            else ArtifactVisibility.AGENT
        )
        values[field.name] = ArtifactRef(
            artifact_ids[field.name],
            LabArtifactKind(field.name).value,
            visibility,
            ArtifactOutcome.PASS,
        )
    return FrictionCapabilityResult(**values)


def _inside_workspace(
    point: tuple[float, float, float],
    applicability: ApplicabilityEnvelope,
) -> bool:
    return all(
        low <= value <= high
        for value, low, high in zip(
            point,
            applicability.workspace_min_m,
            applicability.workspace_max_m,
            strict=True,
        )
    )
