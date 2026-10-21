from __future__ import annotations

import pytest

from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactVisibility,
    PipelineLane,
)
from curious_robot.lab.pipeline import (
    IllegalPipelineTransition,
    LaboratoryPipeline,
    PipelineState,
)


def _put(
    stores: LaboratoryArtifactStores,
    kind: str,
    *,
    provenance=(),  # noqa: ANN001
    fail: bool = False,
    visibility: ArtifactVisibility = ArtifactVisibility.AGENT,
):  # noqa: ANN202
    envelope = ArtifactEnvelope(
        kind=kind,
        payload={"kind": kind},
        outcome=ArtifactOutcome.FAIL if fail else ArtifactOutcome.PASS,
        visibility=visibility,
        provenance=provenance,
        validation_conditions=(f"{kind} schema valid",),
    )
    store = stores.agent if visibility is ArtifactVisibility.AGENT else stores.evaluator
    return store.put(envelope)


def test_illegal_transition_is_rejected_without_state_change(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    pipeline = LaboratoryPipeline(stores)
    wrong_evidence = _put(stores, "twin_build")

    with pytest.raises(IllegalPipelineTransition, match="requires agent capability_gap"):
        pipeline.transition(PipelineState.CAPABILITY_GAP_ASSESSED, evidence=(wrong_evidence,))

    assert pipeline.state is PipelineState.CREATED
    assert pipeline.history == ()


def test_pipeline_exposes_four_future_ui_lanes() -> None:
    assert set(PipelineLane) == {
        PipelineLane.REALITY,
        PipelineLane.TWIN,
        PipelineLane.DEPLOYMENT,
        PipelineLane.CAPABILITY_BUILD,
    }


def test_failure_requires_preserved_fail_artifact_and_is_terminal(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    pipeline = LaboratoryPipeline(stores)

    with pytest.raises(IllegalPipelineTransition, match="FAIL artifact"):
        pipeline.transition(PipelineState.FAILED)
    pass_evidence = _put(stores, "validation")
    with pytest.raises(IllegalPipelineTransition, match="must be FAIL"):
        pipeline.transition(PipelineState.FAILED, evidence=(pass_evidence,))
    failure = _put(stores, "validation", fail=True)
    transition = pipeline.transition(PipelineState.FAILED, evidence=(failure,))

    assert transition.current is PipelineState.FAILED
    with pytest.raises(IllegalPipelineTransition):
        pipeline.transition(PipelineState.CAPABILITY_GAP_ASSESSED, evidence=(pass_evidence,))


def test_happy_path_requires_stored_causally_linked_evidence(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    pipeline = LaboratoryPipeline(stores)
    stages = (
        (PipelineState.CAPABILITY_GAP_ASSESSED, "capability_gap", ArtifactVisibility.AGENT),
        (PipelineState.REALITY_EVIDENCE_CAPTURED, "trajectory_dataset", ArtifactVisibility.AGENT),
        (PipelineState.POSTERIOR_ESTIMATED, "parameter_posterior", ArtifactVisibility.AGENT),
        (PipelineState.TWIN_BUILT, "twin_build", ArtifactVisibility.AGENT),
        (PipelineState.TWIN_VALIDATED, "twin_validation_report", ArtifactVisibility.AGENT),
        (PipelineState.POLICY_VALIDATED, "policy_artifact", ArtifactVisibility.AGENT),
        (PipelineState.REALITY_DEPLOYED, "reality_deployment", ArtifactVisibility.AGENT),
        (PipelineState.EVALUATED, "evaluation_report", ArtifactVisibility.EVALUATOR),
    )
    previous = ()
    for state, kind, visibility in stages:
        evidence = _put(stores, kind, provenance=previous, visibility=visibility)
        pipeline.transition(state, evidence=(evidence,))
        previous = (evidence,)

    assert pipeline.state is PipelineState.EVALUATED
    assert tuple(item.sequence for item in pipeline.history) == tuple(range(1, 9))


def test_forged_or_unlinked_evidence_cannot_advance_pipeline(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    pipeline = LaboratoryPipeline(stores)
    capability = _put(stores, "capability_gap")
    pipeline.transition(PipelineState.CAPABILITY_GAP_ASSESSED, evidence=(capability,))
    unlinked = _put(stores, "trajectory_dataset")

    with pytest.raises(IllegalPipelineTransition, match="descend from"):
        pipeline.transition(PipelineState.REALITY_EVIDENCE_CAPTURED, evidence=(unlinked,))

    forged = ArtifactEnvelope(
        kind="trajectory_dataset",
        payload={},
        provenance=(capability,),
        validation_conditions=("trajectory schema valid",),
    ).ref()
    with pytest.raises(IllegalPipelineTransition, match="not present and valid"):
        pipeline.transition(PipelineState.REALITY_EVIDENCE_CAPTURED, evidence=(forged,))


def test_transition_rejects_extra_evidence_from_another_authority(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    pipeline = LaboratoryPipeline(stores)
    capability = _put(stores, "capability_gap")
    evaluator_report = _put(
        stores,
        "evaluation_report",
        visibility=ArtifactVisibility.EVALUATOR,
    )

    with pytest.raises(IllegalPipelineTransition, match="all capability_gap_assessed evidence"):
        pipeline.transition(
            PipelineState.CAPABILITY_GAP_ASSESSED,
            evidence=(capability, evaluator_report),
        )
