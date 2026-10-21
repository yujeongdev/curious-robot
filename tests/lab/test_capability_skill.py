from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from curious_robot.belief import initial_belief
from curious_robot.lab.artifact_store import LaboratoryArtifactStores, ScopedArtifactStore
from curious_robot.lab.capability_artifact import (
    CapabilityArtifactPayload,
    distill_friction_capability,
    distill_from_manifest,
)
from curious_robot.lab.capability_skill import (
    AttemptObservation,
    DeterministicFrictionSkill,
    SkillStatus,
    execute_from_manifest,
)
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
)
from curious_robot.lab.friction_artifacts import default_world_spec_seed
from curious_robot.lab.friction_capability import FrictionCapabilityResult
from curious_robot.lab.newton_deployment import execute_deployment
from curious_robot.lab.ports import PortAuthority, RuntimeIdentity
from curious_robot.planning import candidate_actions
from curious_robot.protocol import load_default_protocol, protocol_sha256
from curious_robot.schemas import ExperimentEvidence


def _experiment(experiment: str) -> ExperimentEvidence:
    protocol = load_default_protocol()
    static = experiment == "static_friction"
    evidence_id = "static-evidence" if static else "dynamic-evidence"
    metadata = {
        "accepted_contact_sample_count": 0,
        "contact_coverage_fraction": 0.0,
        "controller": (
            "Franka IK minimum-jerk joint-target contact ramp"
            if static
            else "Franka IK minimum-jerk approach plus maintained-contact slide"
        ),
        "experiment_correlation_id": evidence_id,
        "minimum_contact_coverage_fraction": 0.0,
        "minimum_valid_contact_samples": 1,
        "producer": "Newton SensorContact Franka end-effector ↔ cube",
        "protocol_schema_version": protocol.schema_version,
        "protocol_sha256": protocol_sha256(protocol),
        "raw_sample_count": 0,
        "rejected_noncontact_sample_count": 0,
    }
    if static:
        metadata.update(
            {
                "force_model_discrepancy_fraction": 0.1,
                "motion_threshold_m_s": 0.01,
                "position_increment_m": 0.001,
            }
        )
    else:
        metadata["target_velocity_m_s"] = 0.04
    return ExperimentEvidence(
        evidence_id=evidence_id,
        experiment=experiment,
        samples=(),
        metadata=metadata,
    )


def _put(stores, kind, payload, *, provenance=(), evaluator=False):  # noqa: ANN001, ANN202
    visibility = ArtifactVisibility.EVALUATOR if evaluator else ArtifactVisibility.AGENT
    envelope = ArtifactEnvelope(
        kind=kind.value,
        payload=payload,
        visibility=visibility,
        provenance=provenance,
        validation_conditions=(f"{kind.value} fixture is valid",),
    )
    store = stores.evaluator if evaluator else stores.agent
    return store.put(envelope)


def _successful_result(
    stores: LaboratoryArtifactStores,
    *,
    success: bool = True,
    target: tuple[float, float, float] = (0.15, 0.0, 0.025),
):  # noqa: ANN202
    protocol = load_default_protocol()
    seed = default_world_spec_seed(
        initial_position_m=(-0.12, 0.0, 0.025),
        target_position_m=target,
        target_radius_m=0.06,
        world_config={"dt_s": 1 / 240},
        protocol=protocol,
    )
    gap = _put(
        stores,
        LabArtifactKind.CAPABILITY_GAP,
        {
            "capability": "friction-aware-push",
            "missing_evidence_kinds": [],
            "missing_operations": [],
        },
    )
    dataset = _put(
        stores,
        LabArtifactKind.TRAJECTORY_DATASET,
        {
            **seed,
            "experiments": [
                _experiment("static_friction").model_dump(mode="json"),
                _experiment("dynamic_friction").model_dump(mode="json"),
            ],
            "runtime_process_id": 101,
            "world_instance_id": "experiment-world",
        },
        provenance=(gap,),
    )
    belief = initial_belief().model_copy(update={"revision": 2})
    posterior = _put(
        stores,
        LabArtifactKind.PARAMETER_POSTERIOR,
        {"physical_belief": belief.model_dump(mode="json")},
        provenance=(dataset,),
    )
    patch = _put(stores, LabArtifactKind.WORLD_SPEC_PATCH, {}, provenance=(posterior,))
    twin = _put(stores, LabArtifactKind.TWIN_BUILD, {}, provenance=(patch,))
    validation = _put(stores, LabArtifactKind.TWIN_VALIDATION_REPORT, {}, provenance=(twin,))
    action = next(item for item in candidate_actions(target) if item.kind == "push")
    policy = _put(
        stores,
        LabArtifactKind.POLICY_ARTIFACT,
        {
            "action": action.model_dump(mode="json"),
            "goal_target_m": list(target),
            "goal_target_radius_m": 0.06,
            "max_attempts": 2,
            "protocol": protocol.model_dump(mode="json"),
            "protocol_sha256": protocol_sha256(protocol),
        },
        provenance=(validation,),
    )
    deployment = _put(
        stores,
        LabArtifactKind.REALITY_DEPLOYMENT,
        {
            "action_id": action.action_id,
            "attempt_count": 1,
            "attempts": [{"controller_outcome": "completed", "target_error_m": 0.02}],
            "controller_outcome": "completed",
            "fresh_episode": True,
            "observed_final_position_m": [0.13, 0.0, 0.025],
            "peak_contact_force_n": 1.0,
            "prediction_target_error_m": 0.02,
            "runtime_process_id": 101,
            "safety_tripped": False,
            "target_position_m": list(target),
            "target_radius_m": 0.06,
            "terminal": True,
            "world_instance_id": "deployment-world",
        },
        provenance=(policy,),
    )
    evaluation = _put(
        stores,
        LabArtifactKind.EVALUATION_REPORT,
        {"success": success, "terminal": True},
        provenance=(deployment,),
        evaluator=True,
    )
    return FrictionCapabilityResult(
        capability_gap=gap,
        trajectory_dataset=dataset,
        parameter_posterior=posterior,
        world_spec_patch=patch,
        twin_build=twin,
        twin_validation_report=validation,
        policy_artifact=policy,
        reality_deployment=deployment,
        evaluation_report=evaluation,
    )


def test_successful_run_distills_deterministic_capability(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    result = _successful_result(stores)

    first = distill_friction_capability(stores, result)
    second = distill_friction_capability(stores, result)
    capability = stores.agent.get(first)

    assert first == second
    assert capability.provenance[:2] == (result.policy_artifact, result.reality_deployment)
    assert capability.provenance[2].kind == LabArtifactKind.CAPABILITY_ATTESTATION.value
    assert all(item.visibility is ArtifactVisibility.AGENT for item in capability.provenance)
    assert CapabilityArtifactPayload.model_validate(capability.payload).recovery.maximum_attempts == 2


def test_distillation_rejects_unsuccessful_evaluation(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)

    with pytest.raises(ValueError, match="successful terminal"):
        distill_friction_capability(stores, _successful_result(stores, success=False))


def test_deterministic_skill_checks_applicability_and_recovery(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    reference = distill_friction_capability(stores, _successful_result(stores))
    skill = DeterministicFrictionSkill.load(stores.agent, reference)
    available = frozenset({LabOperation.DEPLOY_CAPABILITY.value})

    nominal = skill.begin(
        current_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        available_operations=available,
    )
    outside = skill.begin(
        current_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.01,
        available_operations=available,
    )

    assert nominal.status is SkillStatus.EXECUTE
    assert nominal.request is not None
    assert outside.status is SkillStatus.RETURN_TO_AGENT
    recovery = skill.after_attempt(
        observation=AttemptObservation(
            attempt_count=1,
            target_error_m=0.08,
            measured_force_n=5.0,
            safety_tripped=False,
            controller_completed=True,
        ),
        request=nominal.request,
    )
    assert recovery.request is not None
    exhausted = skill.after_attempt(
        observation=AttemptObservation(
            attempt_count=2,
            target_error_m=0.08,
            measured_force_n=5.0,
            safety_tripped=False,
            controller_completed=True,
        ),
        request=recovery.request,
    )

    assert recovery.status is SkillStatus.EXECUTE
    assert recovery.request.request_id != nominal.request.request_id
    assert exhausted.status is SkillStatus.RETURN_TO_AGENT
    fresh = skill.begin(
        current_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        available_operations=available,
    )
    assert fresh.request is not None
    assert fresh.request.arguments["invocation_id"] != nominal.request.arguments["invocation_id"]

    safety_skill = DeterministicFrictionSkill.load(stores.agent, reference)
    safety_request = safety_skill.begin(
        current_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        available_operations=available,
    ).request
    assert safety_request is not None
    safety_return = safety_skill.after_attempt(
        observation=AttemptObservation(
            attempt_count=1,
            target_error_m=0.08,
            measured_force_n=16.0,
            safety_tripped=True,
            controller_completed=False,
        ),
        request=safety_request,
    )
    assert safety_return.status is SkillStatus.RETURN_TO_AGENT


def test_manifest_cli_adapter_writes_capability_pointer(tmp_path) -> None:
    import json
    from dataclasses import fields

    stores = LaboratoryArtifactStores(tmp_path)
    result = _successful_result(stores)
    (tmp_path / "friction_capability_manifest.json").write_text(
        json.dumps(
            {"artifacts": {field.name: getattr(result, field.name).artifact_id for field in fields(result)}}
        )
    )

    reference = distill_from_manifest(tmp_path)

    pointer = json.loads((tmp_path / "capability_artifact.json").read_text())
    assert pointer["artifact_id"] == reference.artifact_id


def test_distillation_rejects_mixed_run_lineage(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    first = _successful_result(stores)
    second = _successful_result(stores, target=(0.12, 0.02, 0.025))
    mixed = replace(second, trajectory_dataset=first.trajectory_dataset)

    with pytest.raises(ValueError, match="mixed or invalid run lineage"):
        distill_friction_capability(stores, mixed)


def test_skill_load_rejects_forged_capability_provenance(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    valid = distill_friction_capability(stores, _successful_result(stores))
    payload = stores.agent.get(valid).payload
    forged = stores.agent.put(
        ArtifactEnvelope(
            kind=LabArtifactKind.CAPABILITY_ARTIFACT.value,
            payload=payload,
            validation_conditions=("forged capability fixture",),
        )
    )

    with pytest.raises(ValueError, match="provenance"):
        DeterministicFrictionSkill.load(stores.agent, forged)


def test_skill_load_rejects_guardrail_wider_than_source_policy(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    valid = distill_friction_capability(stores, _successful_result(stores))
    envelope = stores.agent.get(valid)
    payload = envelope.payload
    payload["safety"]["maximum_attempts"] = 3
    payload["recovery"]["maximum_attempts"] = 3
    forged = stores.agent.put(
        ArtifactEnvelope(
            kind=LabArtifactKind.CAPABILITY_ARTIFACT.value,
            payload=payload,
            provenance=envelope.provenance,
            validation_conditions=("forged wider guardrail fixture",),
        )
    )

    with pytest.raises(ValueError, match="source artifacts"):
        DeterministicFrictionSkill.load(stores.agent, forged)


def test_skill_fails_closed_on_unvalidated_pose_and_attempt_inputs(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    reference = distill_friction_capability(stores, _successful_result(stores))
    skill = DeterministicFrictionSkill.load(stores.agent, reference)
    available = frozenset({LabOperation.DEPLOY_CAPABILITY.value})

    shifted = skill.begin(
        current_position_m=(-0.11, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        available_operations=available,
    )
    assert shifted.status is SkillStatus.RETURN_TO_AGENT
    with pytest.raises(ValueError):
        AttemptObservation(
            attempt_count=0,
            target_error_m=-0.1,
            measured_force_n=float("nan"),
            safety_tripped=False,
            controller_completed=True,
        )

    nominal = skill.begin(
        current_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        available_operations=available,
    )
    assert nominal.request is not None
    foreign = OperationRequest(
        LabOperation.DEPLOY_CAPABILITY.value,
        {
            "attempt_index": 1,
            "capability_artifact_id": reference.artifact_id,
            "current_position_m": [-0.12, 0.0, 0.025],
            "policy_artifact_id": "sha256:" + "0" * 64,
            "requested_target_m": [0.15, 0.0, 0.025],
            "target_radius_m": 100.0,
        },
        "foreign-request",
    )
    decision = skill.after_attempt(
        observation=AttemptObservation(
            attempt_count=1,
            target_error_m=0.01,
            measured_force_n=0.0,
            safety_tripped=False,
            controller_completed=True,
        ),
        request=foreign,
    )
    assert decision.status is SkillStatus.RETURN_TO_AGENT


def test_execute_boundary_returns_to_agent_and_writes_report_on_reality_error(
    tmp_path,
    monkeypatch,
) -> None:
    import json

    from curious_robot.lab import newton_ports

    stores = LaboratoryArtifactStores(tmp_path)
    reference = distill_friction_capability(stores, _successful_result(stores))
    (tmp_path / "capability_artifact.json").write_text(json.dumps({"artifact_id": reference.artifact_id}))

    class FailingReality:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            raise RuntimeError("PRIVATE-REALITY-DETAIL")

    monkeypatch.setattr(newton_ports, "NewtonSimulatedReality", FailingReality)

    decision = execute_from_manifest(tmp_path)

    report = json.loads((tmp_path / "skill_execution.json").read_text())
    assert decision.status is SkillStatus.RETURN_TO_AGENT
    assert report["status"] == "return_to_agent"
    assert "PRIVATE-REALITY-DETAIL" not in report["reason"]


def test_execute_boundary_reports_corrupt_capability_pointer(tmp_path) -> None:
    import json

    (tmp_path / "capability_artifact.json").write_text(json.dumps({"artifact_id": "not-a-content-id"}))

    decision = execute_from_manifest(tmp_path)

    report = json.loads((tmp_path / "skill_execution.json").read_text())
    assert decision.status is SkillStatus.RETURN_TO_AGENT
    assert report["capability_artifact_id"] is None
    assert report["status"] == "return_to_agent"


def test_execute_boundary_rejects_mismatched_operation_result(tmp_path, monkeypatch) -> None:
    import json

    from curious_robot.lab import newton_ports
    from curious_robot.lab.contracts import OperationResult

    stores = LaboratoryArtifactStores(tmp_path)
    reference = distill_friction_capability(stores, _successful_result(stores))
    (tmp_path / "capability_artifact.json").write_text(json.dumps({"artifact_id": reference.artifact_id}))

    class StaleReality:
        operations = frozenset({LabOperation.DEPLOY_CAPABILITY.value})

        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def execute(self, _request):  # noqa: ANN001, ANN202
            return OperationResult("stale-request", "completed", ())

        def close(self) -> None:
            pass

    monkeypatch.setattr(newton_ports, "NewtonSimulatedReality", StaleReality)

    decision = execute_from_manifest(tmp_path)

    assert decision.status is SkillStatus.RETURN_TO_AGENT
    assert "failed" in decision.reason.lower()


def test_reality_attempt_failure_closes_and_invalidates_invocation(
    tmp_path,
    monkeypatch,
) -> None:
    from curious_robot.lab import newton_deployment

    stores = LaboratoryArtifactStores(tmp_path)
    result = _successful_result(stores)
    capability_ref = distill_friction_capability(stores, result)
    skill = DeterministicFrictionSkill.load(stores.agent, capability_ref)
    request = skill.begin(
        current_position_m=skill.payload.applicability.validated_initial_position_m,
        target_position_m=skill.payload.applicability.validated_target_m,
        target_radius_m=skill.payload.applicability.validated_target_radius_m,
        available_operations=frozenset({LabOperation.DEPLOY_CAPABILITY.value}),
    ).request
    assert request is not None

    class FakeWorld:
        closed = False

        def close(self) -> None:
            self.closed = True

    class FailingTools:
        def execute_safe_push(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("mid-attempt failure")

    world = FakeWorld()

    def fake_world_and_tools(**kwargs):  # noqa: ANN003, ANN202
        kwargs["session"].update({"world": world, "tools": FailingTools(), "config": SimpleNamespace()})
        return world, kwargs["session"]["tools"], kwargs["session"]["config"]

    monkeypatch.setattr(
        newton_deployment,
        "_world_and_tools",
        fake_world_and_tools,
    )
    scoped = ScopedArtifactStore(
        stores.agent,
        read_kinds=frozenset(
            {
                LabArtifactKind.CAPABILITY_ARTIFACT.value,
                LabArtifactKind.CAPABILITY_ATTESTATION.value,
                LabArtifactKind.POLICY_ARTIFACT.value,
                LabArtifactKind.REALITY_DEPLOYMENT.value,
                LabArtifactKind.TRAJECTORY_DATASET.value,
            }
        ),
        write_kinds=frozenset({LabArtifactKind.REALITY_DEPLOYMENT.value}),
    )
    sessions = {}
    identity = RuntimeIdentity(
        PortAuthority.REALITY,
        101,
        "world",
        "model",
        "state",
        "config",
    )

    with pytest.raises(RuntimeError, match="mid-attempt"):
        execute_deployment(
            request=request,
            arguments=request.arguments,
            store=scoped,
            identity=identity,
            hidden_parameters=None,
            hidden_initial_position=(-0.12, 0.0, 0.025),
            experiment_world_id=None,
            capability_sessions=sessions,
            device="fake",
            seed=7,
        )

    session = next(iter(sessions.values()))
    assert session["closed"] is True
    assert world.closed is True
    with pytest.raises(ValueError, match="replayed or reset"):
        execute_deployment(
            request=request,
            arguments=request.arguments,
            store=scoped,
            identity=identity,
            hidden_parameters=None,
            hidden_initial_position=(-0.12, 0.0, 0.025),
            experiment_world_id=None,
            capability_sessions=sessions,
            device="fake",
            seed=7,
        )

    fresh_request = skill.begin(
        current_position_m=skill.payload.applicability.validated_initial_position_m,
        target_position_m=skill.payload.applicability.validated_target_m,
        target_radius_m=skill.payload.applicability.validated_target_radius_m,
        available_operations=frozenset({LabOperation.DEPLOY_CAPABILITY.value}),
    ).request
    assert fresh_request is not None
    with pytest.raises(RuntimeError, match="mid-attempt"):
        execute_deployment(
            request=fresh_request,
            arguments=fresh_request.arguments,
            store=scoped,
            identity=identity,
            hidden_parameters=None,
            hidden_initial_position=(-0.12, 0.0, 0.025),
            experiment_world_id=None,
            capability_sessions=sessions,
            device="fake",
            seed=7,
        )
    assert len(sessions) == 2
