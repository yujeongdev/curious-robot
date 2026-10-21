from __future__ import annotations

import json

import pytest

from curious_robot.lab.artifact_store import (
    ArtifactAuthorityError,
    ArtifactKindPermissionError,
    LaboratoryArtifactStores,
    open_agent_artifact_capability,
)
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactVisibility,
)


def test_artifact_ids_are_deterministic_for_canonical_content(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    left = ArtifactEnvelope(
        kind="trajectory_dataset",
        payload={"b": 2, "a": [1, 3]},
        validation_conditions=("trajectory schema valid",),
    )
    right = ArtifactEnvelope(
        kind="trajectory_dataset",
        payload={"a": [1, 3], "b": 2},
        validation_conditions=("trajectory schema valid",),
    )

    left_ref = stores.agent.put(left)
    right_ref = stores.agent.put(right)

    assert left_ref == right_ref
    assert len(tuple((tmp_path / "agent").rglob("*.json"))) == 1
    assert stores.agent.get(left_ref) == left


def test_validation_failure_is_preserved_as_fail_artifact(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    failure = ArtifactEnvelope(
        kind="twin_validation_report",
        outcome=ArtifactOutcome.FAIL,
        payload={"valid": False, "residual_m": 0.12},
        validation_conditions=("residual_m <= 0.03",),
    )

    reference = stores.agent.put(failure)

    assert reference.outcome is ArtifactOutcome.FAIL
    assert stores.agent.get(reference).outcome is ArtifactOutcome.FAIL
    document = json.loads(next((tmp_path / "agent").rglob("*.json")).read_text())
    assert document["outcome"] == "FAIL"
    assert document["payload"]["residual_m"] == 0.12


def test_evaluator_truth_cannot_enter_or_be_read_from_agent_store(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    hidden_truth = ArtifactEnvelope(
        kind="scenario_truth",
        visibility=ArtifactVisibility.EVALUATOR,
        payload={"mass_kg": 0.42, "mu_static": 0.71},
        validation_conditions=("private scenario schema valid",),
    )
    truth_ref = stores.evaluator.put(hidden_truth)

    with pytest.raises(ArtifactAuthorityError):
        stores.agent.put(hidden_truth)
    with pytest.raises(ArtifactAuthorityError):
        stores.agent.get(truth_ref)
    with pytest.raises(ArtifactAuthorityError):
        stores.agent.put(
            ArtifactEnvelope(
                kind="derived_public_claim",
                payload={"claim": "unsafe lineage"},
                provenance=(truth_ref,),
                validation_conditions=("claim schema valid",),
            )
        )

    assert not list((tmp_path / "agent").rglob("*.json"))
    assert list((tmp_path / "evaluator").rglob("*.json"))


def test_envelope_snapshots_mutable_input() -> None:
    payload = {"samples": [1, 2]}
    envelope = ArtifactEnvelope(
        kind="trajectory_dataset",
        payload=payload,
        validation_conditions=("trajectory schema valid",),
    )
    payload["samples"].append(3)

    assert envelope.payload == {"samples": [1, 2]}


@pytest.mark.parametrize("condition", ["", "   "])
def test_artifact_rejects_blank_validation_condition(condition: str) -> None:
    with pytest.raises(ValueError, match="validation conditions"):
        ArtifactEnvelope(
            kind="trajectory_dataset",
            payload={},
            validation_conditions=(condition,),
        )


def test_schema_rejection_is_a_deterministic_fail_artifact(tmp_path) -> None:
    store = LaboratoryArtifactStores(tmp_path).agent

    first = store.record_validation_failure(
        attempted_kind="world_spec",
        rejected_payload={"friction": float("inf")},
        errors=("friction must be finite",),
        validation_conditions=("all physical scalars are finite",),
    )
    second = store.record_validation_failure(
        attempted_kind="world_spec",
        rejected_payload={"friction": float("inf")},
        errors=("friction must be finite",),
        validation_conditions=("all physical scalars are finite",),
    )

    assert first == second
    assert first.outcome is ArtifactOutcome.FAIL
    assert store.get(first).payload["errors"] == ["friction must be finite"]


def test_scoped_worker_store_enforces_artifact_kind_capabilities(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    capability_gap = stores.agent.put(
        ArtifactEnvelope(
            kind="capability_gap",
            payload={"closed": True},
            validation_conditions=("capability gap is closed",),
        )
    )
    reality_store = open_agent_artifact_capability(
        tmp_path,
        read_kinds=frozenset({"capability_gap"}),
        write_kinds=frozenset({"trajectory_dataset"}),
    )

    assert reality_store.get(capability_gap).kind == "capability_gap"
    with pytest.raises(ArtifactKindPermissionError):
        reality_store.put(
            ArtifactEnvelope(
                kind="scenario_truth",
                payload={"mass_kg": 0.42},
                validation_conditions=("private scenario schema valid",),
            )
        )
