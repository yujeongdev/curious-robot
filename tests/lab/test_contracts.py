from __future__ import annotations

import pytest

from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    CapabilityRequirement,
    OperationRequest,
    PatchOperation,
    StaleWorldSpecError,
    WorldSpec,
    WorldSpecEvidenceError,
    WorldSpecPatch,
)


def _world_document(friction: float = 0.3):
    return {
        "task": {"capability": "friction-aware-push"},
        "public_geometry": {"edge_m": 0.05},
        "numerical_config": {"dt_s": 1 / 240},
        "model_parameters": {"friction": friction},
    }


def _posterior_ref(stores: LaboratoryArtifactStores):
    envelope = ArtifactEnvelope(
        kind="parameter_posterior",
        payload={"friction": 0.45},
        validation_conditions=("posterior interval is physically bounded",),
    )
    return stores.agent.put(envelope)


def test_stale_base_world_spec_patch_is_rejected_without_mutation(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    posterior = _posterior_ref(stores)
    original = WorldSpec.create(
        operation_schema="push-v1",
        document=_world_document(),
    )
    patch = WorldSpecPatch(
        base_spec_id=original.spec_id,
        operations=(PatchOperation("set", ("model_parameters", "friction"), 0.45),),
        evidence=(posterior,),
    )
    current = patch.apply(original, resolver=stores.agent)
    stale_patch = WorldSpecPatch(
        base_spec_id=original.spec_id,
        operations=(PatchOperation("set", ("model_parameters", "friction"), 0.6),),
        evidence=(posterior,),
    )

    with pytest.raises(StaleWorldSpecError):
        stale_patch.apply(current, resolver=stores.agent)

    assert original.document["model_parameters"] == {"friction": 0.3}
    assert current.document["model_parameters"] == {"friction": 0.45}
    assert current.sources == patch.evidence
    assert current.applied_patch_ids == (patch.patch_id,)


def test_world_spec_does_not_retain_mutable_caller_data() -> None:
    source = _world_document()
    spec = WorldSpec.create(operation_schema="push-v1", document=source)
    source["model_parameters"]["friction"] = 9.0
    exposed = spec.document
    exposed["model_parameters"]["friction"] = 8.0

    assert spec.document == _world_document()


def test_patch_and_operation_request_snapshot_mutable_input() -> None:
    patch_value = {"interval": [0.2, 0.5]}
    request_arguments = {"target": [0.1, 0.0, 0.025]}
    operation = PatchOperation("set", ("posterior",), patch_value)
    request = OperationRequest("push", request_arguments, "request-1")
    patch_value["interval"].append(0.8)
    request_arguments["target"][0] = 99.0

    assert operation.value == {"interval": [0.2, 0.5]}
    assert request.arguments == {"target": [0.1, 0.0, 0.025]}


def test_world_spec_patch_cannot_modify_authoritative_geometry_or_use_private_evidence(
    tmp_path,
) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    spec = WorldSpec.create(operation_schema="push-v1", document=_world_document())

    with pytest.raises(ValueError, match="only model_parameters"):
        WorldSpecPatch(
            base_spec_id=spec.spec_id,
            operations=(PatchOperation("set", ("public_geometry", "edge_m"), 0.08),),
            evidence=(_posterior_ref(stores),),
        )

    forged = ArtifactRef(
        "sha256:" + "0" * 64,
        "parameter_posterior",
        ArtifactVisibility.AGENT,
        ArtifactOutcome.PASS,
    )
    patch = WorldSpecPatch(
        base_spec_id=spec.spec_id,
        operations=(PatchOperation("set", ("model_parameters", "friction"), 0.8),),
        evidence=(forged,),
    )
    with pytest.raises(WorldSpecEvidenceError, match="not present and valid"):
        patch.apply(spec, resolver=stores.agent)
    with pytest.raises(ValueError, match="agent-visible PASS"):
        WorldSpecPatch(
            base_spec_id=spec.spec_id,
            operations=(PatchOperation("set", ("model_parameters", "friction"), 0.8),),
            evidence=(
                ArtifactEnvelope(
                    kind="scenario_truth",
                    payload={"friction": 0.8},
                    visibility="evaluator",
                    validation_conditions=("private scenario schema valid",),
                ).ref(),
            ),
        )


def test_capability_gap_is_computed_before_building() -> None:
    requirement = CapabilityRequirement(
        capability="friction-aware-push",
        required_operations=("observe_pose", "push", "hold"),
        required_evidence_kinds=("trajectory_dataset", "force_trace"),
    )

    gap = requirement.assess(
        available_operations=("observe_pose", "push"),
        available_evidence_kinds=("trajectory_dataset",),
    )

    assert gap.missing_operations == ("hold",)
    assert gap.missing_evidence_kinds == ("force_trace",)
    assert not gap.is_closed
