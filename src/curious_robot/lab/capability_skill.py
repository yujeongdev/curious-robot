"""LLM-free runtime for a validated friction capability."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from curious_robot.protocol import PhysicalExperimentProtocol
from curious_robot.schemas import ActionCandidate

from .artifact_store import ContentAddressedArtifactStore, LaboratoryArtifactStores
from .capability_artifact import (
    CapabilityArtifactPayload,
    CapabilityAttestationPayload,
    _inside_workspace,
)
from .contracts import (
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
)
from .friction_artifacts import RealityDeploymentPayload, TrajectoryDatasetPayload


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

class AttemptObservation(_RuntimeModel):
    attempt_count: int = Field(gt=0, le=3)
    target_error_m: float = Field(ge=0.0)
    measured_force_n: float = Field(ge=0.0)
    safety_tripped: bool
    controller_completed: bool


class SkillStatus(StrEnum):
    EXECUTE = "execute"
    COMPLETED = "completed"
    RETURN_TO_AGENT = "return_to_agent"


@dataclass(frozen=True, slots=True)
class SkillDecision:
    status: SkillStatus
    reason: str
    request: OperationRequest | None = None


class DeterministicFrictionSkill:
    """Pure decision policy; out-of-envelope conditions return control to the agent."""

    def __init__(self, capability_ref: ArtifactRef, payload: CapabilityArtifactPayload) -> None:
        self.capability_ref = capability_ref
        self.payload = payload
        self._active_request: OperationRequest | None = None

    @classmethod
    def load(
        cls,
        store: ContentAddressedArtifactStore,
        reference: ArtifactRef,
    ) -> DeterministicFrictionSkill:
        if (
            reference.kind != LabArtifactKind.CAPABILITY_ARTIFACT.value
            or reference.visibility is not ArtifactVisibility.AGENT
            or reference.outcome is not ArtifactOutcome.PASS
        ):
            raise ValueError("skill requires an agent PASS capability_artifact")
        envelope = store.get(reference)
        payload = CapabilityArtifactPayload.model_validate(envelope.payload)
        policy_ref = ArtifactRef(
            payload.policy_artifact_id,
            LabArtifactKind.POLICY_ARTIFACT.value,
            ArtifactVisibility.AGENT,
            ArtifactOutcome.PASS,
        )
        deployment_ref = ArtifactRef(
            payload.source_deployment_id,
            LabArtifactKind.REALITY_DEPLOYMENT.value,
            ArtifactVisibility.AGENT,
            ArtifactOutcome.PASS,
        )
        attestation_ref = ArtifactRef(
            payload.attestation_id,
            LabArtifactKind.CAPABILITY_ATTESTATION.value,
            ArtifactVisibility.AGENT,
            ArtifactOutcome.PASS,
        )
        dataset_ref = ArtifactRef(
            payload.source_trajectory_dataset_id,
            LabArtifactKind.TRAJECTORY_DATASET.value,
            ArtifactVisibility.AGENT,
            ArtifactOutcome.PASS,
        )
        if envelope.provenance != (policy_ref, deployment_ref, attestation_ref, dataset_ref):
            raise ValueError("capability provenance does not match its source IDs")
        policy = store.get(policy_ref)
        deployment = store.get(deployment_ref)
        attestation = store.get(attestation_ref)
        dataset = TrajectoryDatasetPayload.model_validate(store.get(dataset_ref).payload)
        if deployment.provenance != (policy_ref,) or attestation.provenance != (deployment_ref,):
            raise ValueError("capability source lineage is invalid")
        attestation_payload = CapabilityAttestationPayload.model_validate(attestation.payload)
        if attestation_payload.deployment_id != deployment_ref.artifact_id:
            raise ValueError("capability attestation is invalid")
        if (
            attestation_payload.evaluation_report_commitment
            != payload.evaluation_report_commitment
        ):
            raise ValueError("capability evaluation commitment is invalid")
        if not isinstance(policy.payload, dict):
            raise ValueError("capability policy payload must be an object")
        source_action = ActionCandidate.model_validate(policy.payload["action"])
        source_protocol = PhysicalExperimentProtocol.model_validate(policy.payload["protocol"])
        source_deployment = RealityDeploymentPayload.model_validate(deployment.payload)
        expected_tolerance = 3.0 * dataset.numerical_config.observation_noise_std + 1e-6
        expected_workspace_min = tuple(
            min(a - expected_tolerance, b)
            for a, b in zip(
                dataset.public_geometry.initial_position_m,
                dataset.task.target_position_m,
                strict=True,
            )
        )
        expected_workspace_max = tuple(
            max(a + expected_tolerance, b)
            for a, b in zip(
                dataset.public_geometry.initial_position_m,
                dataset.task.target_position_m,
                strict=True,
            )
        )
        if (
            source_action != payload.action_template
            or source_protocol != payload.protocol
            or policy.payload["protocol_sha256"] != payload.protocol_sha256
            or tuple(policy.payload["goal_target_m"])
            != payload.applicability.validated_target_m
            or float(policy.payload["goal_target_radius_m"])
            != payload.applicability.validated_target_radius_m
            or int(policy.payload["max_attempts"]) != payload.recovery.maximum_attempts
            or payload.safety.maximum_attempts != payload.recovery.maximum_attempts
            or payload.safety.maximum_force_n != source_action.max_force_n
            or payload.safety.maximum_speed_m_s != source_action.speed_m_s
            or source_deployment.action_id != source_action.action_id
            or source_deployment.target_position_m != source_action.target_m
            or source_deployment.target_radius_m
            != payload.applicability.validated_target_radius_m
            or payload.applicability.validated_initial_position_m
            != dataset.public_geometry.initial_position_m
            or payload.applicability.initial_position_tolerance_m != expected_tolerance
            or payload.applicability.workspace_min_m != expected_workspace_min
            or payload.applicability.workspace_max_m != expected_workspace_max
            or payload.applicability.maximum_planar_travel_m
            != math.dist(
                dataset.public_geometry.initial_position_m[:2],
                dataset.task.target_position_m[:2],
            )
            + expected_tolerance
        ):
            raise ValueError("capability semantics do not match source artifacts")
        return cls(reference, payload)

    def begin(
        self,
        *,
        current_position_m: tuple[float, float, float],
        target_position_m: tuple[float, float, float],
        target_radius_m: float,
        available_operations: frozenset[str],
    ) -> SkillDecision:
        applicability = self.payload.applicability
        missing = [
            operation.value
            for operation in applicability.required_operations
            if operation.value not in available_operations
        ]
        if missing:
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, f"missing operations: {missing}")
        if target_radius_m != applicability.validated_target_radius_m:
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "target radius was not validated")
        if (
            math.dist(current_position_m, applicability.validated_initial_position_m)
            > applicability.initial_position_tolerance_m
        ):
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "initial pose was not validated")
        if target_position_m != applicability.validated_target_m:
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "target was not validated by this skill")
        if not _inside_workspace(current_position_m, applicability):
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "current pose is outside applicability")
        if not _inside_workspace(target_position_m, applicability):
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "target is outside applicability")
        if math.dist(current_position_m[:2], target_position_m[:2]) > applicability.maximum_planar_travel_m:
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "requested travel exceeds validation")
        request = OperationRequest(
            operation=LabOperation.DEPLOY_CAPABILITY.value,
            arguments={
                "attempt_index": 1,
                "capability_artifact_id": self.capability_ref.artifact_id,
                "current_position_m": list(current_position_m),
                "invocation_id": str(uuid4()),
                "policy_artifact_id": self.payload.policy_artifact_id,
                "requested_target_m": list(target_position_m),
                "target_radius_m": target_radius_m,
            },
            request_id=str(uuid4()),
        )
        self._active_request = request
        return SkillDecision(SkillStatus.EXECUTE, "within validated applicability envelope", request)

    def after_attempt(
        self,
        *,
        observation: AttemptObservation,
        request: OperationRequest,
    ) -> SkillDecision:
        if self._active_request is None or request != self._active_request:
            self._active_request = None
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "attempt request is not skill-bound")
        arguments = request.arguments
        try:
            target_radius_m = float(arguments.get("target_radius_m", -1.0))
            attempt_index = int(arguments.get("attempt_index", -1))
        except (AttributeError, TypeError, ValueError):
            arguments = None
            target_radius_m = -1.0
            attempt_index = -1
        if not isinstance(arguments, dict) or (
            not math.isfinite(target_radius_m)
            or request.operation != LabOperation.DEPLOY_CAPABILITY.value
            or arguments.get("capability_artifact_id") != self.capability_ref.artifact_id
            or arguments.get("policy_artifact_id") != self.payload.policy_artifact_id
            or tuple(arguments.get("requested_target_m", ()))
            != self.payload.applicability.validated_target_m
            or target_radius_m != self.payload.applicability.validated_target_radius_m
            or attempt_index != observation.attempt_count
            or observation.attempt_count > self.payload.recovery.maximum_attempts
        ):
            self._active_request = None
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "attempt request contract is invalid")
        if (
            observation.target_error_m <= target_radius_m
            and observation.controller_completed
            and not observation.safety_tripped
        ):
            self._active_request = None
            return SkillDecision(SkillStatus.COMPLETED, "target predicate satisfied")
        if (
            observation.safety_tripped
            or observation.measured_force_n > self.payload.safety.maximum_force_n
        ):
            self._active_request = None
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "independent safety guardrail fired")
        if not observation.controller_completed:
            self._active_request = None
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "controller did not complete")
        if observation.attempt_count >= self.payload.recovery.maximum_attempts:
            self._active_request = None
            return SkillDecision(SkillStatus.RETURN_TO_AGENT, "recovery attempts exhausted")
        recovery = OperationRequest(
            operation=LabOperation.DEPLOY_CAPABILITY.value,
            arguments={**arguments, "attempt_index": observation.attempt_count + 1},
            request_id=str(uuid4()),
        )
        self._active_request = recovery
        return SkillDecision(SkillStatus.EXECUTE, "bounded recovery attempt", recovery)


def execute_from_manifest(
    run_directory: str | Path,
    *,
    device: str = "cuda:0",
    seed: int = 7,
) -> SkillDecision:
    """Execute a distilled skill through Reality's independent capability guardrail."""

    from .newton_ports import NewtonSimulatedReality

    root = Path(run_directory)
    stores = LaboratoryArtifactStores(root)
    reality = None
    reference = None
    deployment_refs = []
    final = SkillDecision(SkillStatus.RETURN_TO_AGENT, "capability did not start")
    try:
        pointer = json.loads((root / "capability_artifact.json").read_text())
        reference = ArtifactRef(
            pointer["artifact_id"],
            LabArtifactKind.CAPABILITY_ARTIFACT.value,
            ArtifactVisibility.AGENT,
            ArtifactOutcome.PASS,
        )
        skill = DeterministicFrictionSkill.load(stores.agent, reference)
        applicability = skill.payload.applicability
        reality = NewtonSimulatedReality(root, device=device, seed=seed)
        decision = skill.begin(
            current_position_m=applicability.validated_initial_position_m,
            target_position_m=applicability.validated_target_m,
            target_radius_m=applicability.validated_target_radius_m,
            available_operations=reality.operations,
        )
        final = decision
        while final.status is SkillStatus.EXECUTE and final.request is not None:
            current_request = final.request
            outcome = reality.execute(current_request)
            if (
                outcome.request_id != current_request.request_id
                or outcome.status != "completed"
                or len(outcome.evidence) != 1
            ):
                final = SkillDecision(
                    SkillStatus.RETURN_TO_AGENT,
                    "Reality capability execution failed",
                )
                break
            deployment_ref = outcome.evidence[0]
            if (
                deployment_ref.kind != LabArtifactKind.REALITY_DEPLOYMENT.value
                or deployment_ref.visibility is not ArtifactVisibility.AGENT
                or deployment_ref.outcome is not ArtifactOutcome.PASS
            ):
                final = SkillDecision(
                    SkillStatus.RETURN_TO_AGENT,
                    "Reality returned an invalid deployment reference",
                )
                break
            deployment_refs.append(deployment_ref.artifact_id)
            deployment_envelope = stores.agent.get(deployment_ref)
            deployment = RealityDeploymentPayload.model_validate(
                deployment_envelope.payload
            )
            arguments = current_request.arguments
            if not isinstance(arguments, dict):
                raise ValueError("active capability request arguments must be an object")
            policy_ref = ArtifactRef(
                skill.payload.policy_artifact_id,
                LabArtifactKind.POLICY_ARTIFACT.value,
                ArtifactVisibility.AGENT,
                ArtifactOutcome.PASS,
            )
            if (
                deployment_envelope.provenance != (policy_ref, reference)
                or deployment.request_id != current_request.request_id
                or deployment.invocation_id != arguments["invocation_id"]
                or deployment.attempt_index != arguments["attempt_index"]
            ):
                final = SkillDecision(
                    SkillStatus.RETURN_TO_AGENT,
                    "Reality deployment is not bound to the active attempt",
                )
                break
            final = skill.after_attempt(
                observation=AttemptObservation(
                    attempt_count=deployment.attempt_count,
                    target_error_m=deployment.prediction_target_error_m,
                    measured_force_n=deployment.peak_contact_force_n,
                    safety_tripped=deployment.safety_tripped,
                    controller_completed=deployment.controller_outcome == "completed",
                ),
                request=current_request,
            )
    except Exception as error:
        final = SkillDecision(
            SkillStatus.RETURN_TO_AGENT,
            f"Reality execution failed with {type(error).__name__}",
        )
    finally:
        if reality is not None:
            reality.close()
    (root / "skill_execution.json").write_text(
        json.dumps(
            {
                    "capability_artifact_id": (
                        reference.artifact_id if reference is not None else None
                    ),
                "deployment_artifact_ids": deployment_refs,
                "reason": final.reason,
                "status": final.status.value,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return final
