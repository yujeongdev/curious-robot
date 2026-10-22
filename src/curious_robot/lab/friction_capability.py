"""One evidence-gated friction capability from Reality through evaluation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from uuid import uuid4

from .artifact_store import LaboratoryArtifactStores
from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    CapabilityRequirement,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
    OperationResult,
)
from .friction_artifacts import (
    CapabilityExecutionError,
    FrictionArtifactBuilder,
    RealityDeploymentPayload,
    TrajectoryDatasetPayload,
)
from .pipeline import IllegalPipelineTransition, LaboratoryPipeline, PipelineState
from .ports import (
    EvaluatorPort,
    PortAuthority,
    RealityPort,
    TwinPort,
    assert_independent_reality_and_twin,
)

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class FrictionCapabilityResult:
    capability_gap: ArtifactRef
    trajectory_dataset: ArtifactRef
    parameter_posterior: ArtifactRef
    world_spec_patch: ArtifactRef
    twin_build: ArtifactRef
    twin_validation_report: ArtifactRef
    policy_artifact: ArtifactRef
    reality_deployment: ArtifactRef
    evaluation_report: ArtifactRef


class FrictionCapabilityPipeline:
    """Coordinate ports using only public artifacts and an explicit CAS lineage."""

    REQUIRED_OPERATIONS = tuple(
        operation.value
        for operation in (
            LabOperation.FRICTION_EXPERIMENT,
            LabOperation.BUILD_TWIN,
            LabOperation.VALIDATE_POLICY,
            LabOperation.DEPLOY_POLICY,
            LabOperation.EVALUATE_TERMINAL,
        )
    )

    def __init__(
        self,
        *,
        stores: LaboratoryArtifactStores,
        reality: RealityPort,
        twin: TwinPort,
        evaluator: EvaluatorPort,
        task_id: str = "push-cube-v1",
    ) -> None:
        assert_independent_reality_and_twin(reality.identity, twin.identity)
        if evaluator.identity.authority is not PortAuthority.EVALUATOR:
            raise ValueError("evaluator identity must carry evaluator authority")
        if evaluator.identity.process_id in {reality.identity.process_id, twin.identity.process_id}:
            raise ValueError("Evaluator must run in a process distinct from Reality and Twin")
        self.stores = stores
        self.reality = reality
        self.twin = twin
        self.evaluator = evaluator
        self.task_id = task_id
        self.pipeline = LaboratoryPipeline(stores)
        self.artifacts = FrictionArtifactBuilder(stores)

    def run(self) -> FrictionCapabilityResult:
        capability_gap = self._assess_capability_gap()
        self._advance(
            PipelineState.CAPABILITY_GAP_ASSESSED,
            evidence=(capability_gap,),
        )

        trajectory_dataset = self._completed_result(
            self._invoke_port(
                attempted_kind=LabArtifactKind.TRAJECTORY_DATASET.value,
                provenance=(capability_gap,),
                operation=lambda: self.reality.execute(
                    OperationRequest(
                        LabOperation.FRICTION_EXPERIMENT.value,
                        {"provenance": [capability_gap.artifact_id], "task_id": self.task_id},
                        str(uuid4()),
                    )
                ),
            ),
            expected_kind=LabArtifactKind.TRAJECTORY_DATASET.value,
        )
        self._advance(
            PipelineState.REALITY_EVIDENCE_CAPTURED,
            evidence=(trajectory_dataset,),
        )

        parameter_posterior = self._invoke_step(
            attempted_kind=LabArtifactKind.PARAMETER_POSTERIOR.value,
            provenance=(trajectory_dataset,),
            operation=lambda: self.artifacts.estimate_posterior(trajectory_dataset),
        )
        self._advance(
            PipelineState.POSTERIOR_ESTIMATED,
            evidence=(parameter_posterior,),
        )

        world_spec, patch_ref = self._invoke_step(
            attempted_kind=LabArtifactKind.WORLD_SPEC_PATCH.value,
            provenance=(parameter_posterior,),
            operation=lambda: self.artifacts.build_world_spec(
                trajectory_dataset,
                parameter_posterior,
            ),
        )
        twin_build = self._invoke_port(
            attempted_kind=LabArtifactKind.TWIN_BUILD.value,
            provenance=(parameter_posterior,),
            operation=lambda: self.twin.build(world_spec),
        )
        self._advance(PipelineState.TWIN_BUILT, evidence=(twin_build,))

        twin_validation = self._completed_result(
            self._invoke_port(
                attempted_kind=LabArtifactKind.TWIN_VALIDATION_REPORT.value,
                provenance=(twin_build,),
                operation=lambda: self.twin.execute(
                    OperationRequest(
                        LabOperation.VALIDATE_POLICY.value,
                        {"twin_build_id": twin_build.artifact_id},
                        str(uuid4()),
                    )
                ),
            ),
            expected_kind=LabArtifactKind.TWIN_VALIDATION_REPORT.value,
        )
        self._advance(PipelineState.TWIN_VALIDATED, evidence=(twin_validation,))

        policy = self._invoke_step(
            attempted_kind=LabArtifactKind.POLICY_ARTIFACT.value,
            provenance=(twin_validation,),
            operation=lambda: self.artifacts.distill_policy(twin_validation),
        )
        self._advance(PipelineState.POLICY_VALIDATED, evidence=(policy,))

        deployment = self._completed_result(
            self._invoke_port(
                attempted_kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
                provenance=(policy,),
                operation=lambda: self.reality.execute(
                    OperationRequest(
                        LabOperation.DEPLOY_POLICY.value,
                        {"policy_artifact_id": policy.artifact_id},
                        str(uuid4()),
                    )
                ),
            ),
            expected_kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
        )
        self._verify_fresh_reality_episode(trajectory_dataset, deployment, policy)
        self._advance(PipelineState.REALITY_DEPLOYED, evidence=(deployment,))

        evaluation = self._invoke_port(
            attempted_kind=LabArtifactKind.EVALUATION_REPORT.value,
            provenance=(deployment,),
            operation=lambda: self.evaluator.evaluate_terminal(deployment),
        )
        self._advance(PipelineState.EVALUATED, evidence=(evaluation,))
        return FrictionCapabilityResult(
            capability_gap=capability_gap,
            trajectory_dataset=trajectory_dataset,
            parameter_posterior=parameter_posterior,
            world_spec_patch=patch_ref,
            twin_build=twin_build,
            twin_validation_report=twin_validation,
            policy_artifact=policy,
            reality_deployment=deployment,
            evaluation_report=evaluation,
        )

    def _assess_capability_gap(self) -> ArtifactRef:
        available = tuple(sorted(self.reality.operations | self.twin.operations | self.evaluator.operations))
        available_evidence = tuple(
            sorted(self.reality.evidence_kinds | self.twin.evidence_kinds | self.evaluator.evidence_kinds)
        )
        requirement = CapabilityRequirement(
            capability=(
                "friction-aware-push"
                if self.task_id == "push-cube-v1"
                else f"friction-aware-push:{self.task_id}"
            ),
            required_operations=self.REQUIRED_OPERATIONS,
            required_evidence_kinds=(
                LabArtifactKind.TRAJECTORY_DATASET.value,
                LabArtifactKind.TWIN_VALIDATION_REPORT.value,
            ),
        )
        gap = requirement.assess(
            available_operations=available,
            available_evidence_kinds=available_evidence,
        )
        envelope = ArtifactEnvelope(
            kind=LabArtifactKind.CAPABILITY_GAP.value,
            payload={
                "capability": requirement.capability,
                "available_operations": list(available),
                "missing_evidence_kinds": list(gap.missing_evidence_kinds),
                "missing_operations": list(gap.missing_operations),
            },
            validation_conditions=("all required operations and evidence producers are available",),
        )
        if not gap.is_closed:
            failure = self.stores.agent.record_validation_failure(
                attempted_kind=LabArtifactKind.CAPABILITY_GAP.value,
                rejected_payload=envelope.payload,
                errors=("capability gap is not closed",),
                validation_conditions=("all required operations and evidence producers are available",),
            )
            self.pipeline.transition(PipelineState.FAILED, evidence=(failure,))
            raise CapabilityExecutionError(f"capability gap remains open: {gap}")
        return self.stores.agent.put(envelope)

    def _verify_fresh_reality_episode(
        self,
        dataset_ref: ArtifactRef,
        deployment_ref: ArtifactRef,
        policy_ref: ArtifactRef,
    ) -> None:
        dataset = TrajectoryDatasetPayload.model_validate(self.stores.agent.get(dataset_ref).payload)
        deployment = RealityDeploymentPayload.model_validate(self.stores.agent.get(deployment_ref).payload)
        identity_matches = (
            deployment.task_id == dataset.task.task_id
            and deployment.task_version == dataset.task.task_version
            and deployment.object_id == dataset.task.object_id
            and deployment.object_archetype_version == dataset.task.object_archetype_version
            and deployment.target_position_m == dataset.task.target_position_m
            and deployment.target_radius_m == dataset.task.target_radius_m
            and deployment.target_yaw_rad == dataset.task.target_yaw_rad
            and deployment.yaw_tolerance_rad == dataset.task.yaw_tolerance_rad
            and (
                deployment.geometry_hash == dataset.task.geometry_hash
                or (
                    dataset.task.task_id == "push-cube-v1"
                    and deployment.geometry_hash is None
                )
            )
        )
        is_fresh = (
            deployment.fresh_episode
            and deployment.world_instance_id != dataset.world_instance_id
            and identity_matches
        )
        if is_fresh:
            return
        failure = self.stores.agent.record_validation_failure(
            attempted_kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
            rejected_payload=deployment.model_dump(mode="json"),
            errors=("policy deployment is stale or task/object geometry identity changed",),
            provenance=(policy_ref,),
            validation_conditions=(
                "deployment world identity differs from experiment world",
                "deployment task and public geometry identity match the validated policy",
            ),
        )
        self.pipeline.transition(PipelineState.FAILED, evidence=(failure,))
        raise CapabilityExecutionError("policy deployment did not use a fresh Reality episode")

    def _completed_result(self, result: OperationResult, *, expected_kind: str) -> ArtifactRef:
        if result.status != "completed" or len(result.evidence) != 1:
            fail_refs = tuple(
                reference for reference in result.evidence if reference.outcome is ArtifactOutcome.FAIL
            )
            if fail_refs and len(fail_refs) == len(result.evidence):
                try:
                    for reference in fail_refs:
                        self.stores.get(reference)
                except Exception as error:
                    self._preserve_failure(
                        attempted_kind=expected_kind,
                        provenance=self.pipeline.history[-1].evidence,
                        error_type=type(error).__name__,
                    )
                    raise CapabilityExecutionError(
                        f"operation returned missing or invalid FAIL evidence for {expected_kind}"
                    ) from error
                self.pipeline.transition(PipelineState.FAILED, evidence=fail_refs)
            else:
                self._preserve_failure(
                    attempted_kind=expected_kind,
                    provenance=self.pipeline.history[-1].evidence,
                    error_type=f"operation_status_{result.status}",
                )
            raise CapabilityExecutionError(
                f"operation {result.request_id} did not complete with one evidence artifact: {result.status}"
            )
        reference = result.evidence[0]
        if reference.kind != expected_kind or reference.visibility is not ArtifactVisibility.AGENT:
            self._preserve_failure(
                attempted_kind=expected_kind,
                provenance=self.pipeline.history[-1].evidence,
                error_type="invalid_operation_evidence_contract",
            )
            raise CapabilityExecutionError(
                f"operation returned {reference.visibility.value} {reference.kind}; "
                f"expected agent {expected_kind}"
            )
        try:
            self.stores.agent.get(reference)
        except Exception as error:
            self._preserve_failure(
                attempted_kind=expected_kind,
                provenance=self.pipeline.history[-1].evidence,
                error_type=type(error).__name__,
            )
            raise CapabilityExecutionError(
                f"operation returned missing or invalid {expected_kind} evidence"
            ) from error
        return reference

    def _advance(
        self,
        target: PipelineState,
        *,
        evidence: tuple[ArtifactRef, ...],
    ) -> None:
        prior = self.pipeline.history[-1].evidence if self.pipeline.history else ()
        try:
            self.pipeline.transition(target, evidence=evidence)
        except IllegalPipelineTransition as error:
            self._preserve_failure(
                attempted_kind=f"pipeline_transition.{target.value}",
                provenance=prior,
                error_type=type(error).__name__,
            )
            raise CapabilityExecutionError(f"pipeline rejected transition to {target.value}") from error

    def _invoke_port(
        self,
        *,
        attempted_kind: str,
        provenance: tuple[ArtifactRef, ...],
        operation: Callable[[], _T],
    ) -> _T:
        try:
            return operation()
        except Exception as error:
            self._preserve_failure(
                attempted_kind=attempted_kind,
                provenance=provenance,
                error_type=type(error).__name__,
            )
            raise CapabilityExecutionError(
                f"{attempted_kind} port operation failed with {type(error).__name__}"
            ) from error

    def _invoke_step(
        self,
        *,
        attempted_kind: str,
        provenance: tuple[ArtifactRef, ...],
        operation: Callable[[], _T],
    ) -> _T:
        try:
            return operation()
        except CapabilityExecutionError:
            if self.pipeline.state is not PipelineState.FAILED:
                self._preserve_failure(
                    attempted_kind=attempted_kind,
                    provenance=provenance,
                    error_type="CapabilityExecutionError",
                )
            raise
        except Exception as error:
            self._preserve_failure(
                attempted_kind=attempted_kind,
                provenance=provenance,
                error_type=type(error).__name__,
            )
            raise CapabilityExecutionError(
                f"{attempted_kind} pipeline step failed with {type(error).__name__}"
            ) from error

    def _preserve_failure(
        self,
        *,
        attempted_kind: str,
        provenance: tuple[ArtifactRef, ...],
        error_type: str,
    ) -> ArtifactRef:
        failure = self.stores.agent.record_validation_failure(
            attempted_kind=attempted_kind,
            rejected_payload={"attempted_kind": attempted_kind},
            errors=(error_type,),
            provenance=provenance,
            validation_conditions=(f"{attempted_kind} operation completes successfully",),
        )
        if self.pipeline.state is not PipelineState.FAILED:
            self.pipeline.transition(PipelineState.FAILED, evidence=(failure,))
        return failure
