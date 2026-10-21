from __future__ import annotations

from dataclasses import dataclass

import pytest

from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    OperationRequest,
    OperationResult,
    WorldSpec,
)
from curious_robot.lab.friction_artifacts import (
    CapabilityExecutionError,
    FrictionTaskSpec,
    RealityDeploymentPayload,
    TrajectoryDatasetPayload,
    default_world_spec_seed,
    select_push_policy,
)
from curious_robot.lab.friction_capability import FrictionCapabilityPipeline
from curious_robot.lab.pipeline import PipelineState
from curious_robot.lab.ports import PortAuthority, RuntimeIdentity
from curious_robot.protocol import load_default_protocol, protocol_sha256
from curious_robot.schemas import ExperimentEvidence, MotionSample, PhysicalBelief


def _ref(artifact_id: str, kind: str, visibility=ArtifactVisibility.AGENT) -> ArtifactRef:  # noqa: ANN001
    return ArtifactRef(artifact_id, kind, visibility, ArtifactOutcome.PASS)


def _sample(index: int, *, dynamic: bool) -> MotionSample:
    velocity = 0.03 if dynamic or index >= 5 else 0.0
    return MotionSample(
        timestamp_s=0.02 * index,
        position_m=(0.001 * index, 0.0, 0.025),
        velocity_m_s=(velocity, 0.0, 0.0),
        measured_force_n=(1.0 + 0.01 * index, 0.0, 0.0),
        contact_valid=True,
        contact_pair=("fr3_tool_tip_collision", "unknown_cube_collision"),
        contact_position_m=(0.001 * index, 0.0, 0.025),
        contact_force_producer="fake-native-contact",
        experiment_id="dynamic" if dynamic else "static",
        command_id=f"command-{index}",
        experiment_phase="contact",
    )


def _experiments() -> tuple[ExperimentEvidence, ExperimentEvidence]:
    protocol = load_default_protocol()
    common = {
        "producer": "Newton SensorContact Franka end-effector ↔ cube",
        "protocol_schema_version": protocol.schema_version,
        "protocol_sha256": protocol_sha256(protocol),
        "raw_sample_count": 20,
        "accepted_contact_sample_count": 20,
        "rejected_noncontact_sample_count": 0,
        "contact_coverage_fraction": 1.0,
        "minimum_contact_coverage_fraction": 0.5,
        "minimum_valid_contact_samples": 5,
    }
    return (
        ExperimentEvidence(
            evidence_id="static-evidence",
            experiment="static_friction",
            samples=tuple(_sample(index, dynamic=False) for index in range(12)),
            metadata={
                **common,
                "accepted_contact_sample_count": 12,
                "controller": "Franka IK minimum-jerk joint-target contact ramp",
                "experiment_correlation_id": "static-evidence",
                "force_model_discrepancy_fraction": 0.1,
                "motion_threshold_m_s": 0.01,
                "position_increment_m": 0.001,
                "raw_sample_count": 12,
            },
        ),
        ExperimentEvidence(
            evidence_id="dynamic-evidence",
            experiment="dynamic_friction",
            samples=tuple(_sample(index, dynamic=True) for index in range(20)),
            metadata={
                **common,
                "controller": "Franka IK minimum-jerk approach plus maintained-contact slide",
                "experiment_correlation_id": "dynamic-evidence",
                "target_velocity_m_s": 0.03,
            },
        ),
    )


@dataclass
class FakeRealityPort:
    stores: LaboratoryArtifactStores
    fresh_deployment: bool = True
    operations = frozenset({"friction_experiment", "deploy_policy"})
    evidence_kinds = frozenset({"trajectory_dataset", "reality_deployment"})
    identity = RuntimeIdentity(
        PortAuthority.REALITY,
        101,
        "reality-service",
        "reality-model",
        "reality-state",
        "reality-config",
    )

    def execute(self, request: OperationRequest) -> OperationResult:
        arguments = request.arguments
        assert isinstance(arguments, dict)
        if request.operation == "friction_experiment":
            capability_ref = _ref(arguments["provenance"][0], "capability_gap")
            seed = default_world_spec_seed(
                initial_position_m=(-0.12, 0.0, 0.025),
                target_position_m=(0.15, 0.0, 0.025),
                target_radius_m=0.06,
                world_config={"dt_s": 1 / 240},
            )
            reference = self.stores.agent.put(
                ArtifactEnvelope(
                    kind="trajectory_dataset",
                    payload={
                        **seed,
                        "experiments": [item.model_dump(mode="json") for item in _experiments()],
                        "runtime_process_id": self.identity.process_id,
                        "world_instance_id": "reality-experiment-world",
                    },
                    provenance=(capability_ref,),
                    validation_conditions=("native contact coverage passes protocol",),
                )
            )
            return OperationResult(request.request_id, "completed", (reference,))
        if request.operation == "deploy_policy":
            policy_ref = _ref(arguments["policy_artifact_id"], "policy_artifact")
            self.stores.agent.get(policy_ref)
            world_id = "reality-deployment-world" if self.fresh_deployment else "reality-experiment-world"
            reference = self.stores.agent.put(
                ArtifactEnvelope(
                    kind="reality_deployment",
                    payload={
                        "action_id": "push_straight_low",
                        "attempt_count": 1,
                        "attempts": [
                            {"controller_outcome": "completed", "target_error_m": 0.0}
                        ],
                        "controller_outcome": "completed",
                        "fresh_episode": self.fresh_deployment,
                        "observed_final_position_m": [0.15, 0.0, 0.025],
                        "prediction_target_error_m": 0.0,
                        "runtime_process_id": self.identity.process_id,
                        "safety_tripped": False,
                        "target_position_m": [0.15, 0.0, 0.025],
                        "target_radius_m": 0.06,
                        "terminal": True,
                        "world_instance_id": world_id,
                    },
                    provenance=(policy_ref,),
                    validation_conditions=("independent safety guardrail did not trip",),
                )
            )
            return OperationResult(request.request_id, "completed", (reference,))
        return OperationResult(request.request_id, "rejected", detail="unsupported operation")

    def capture_evidence(self) -> tuple[ArtifactRef, ...]:
        return ()


@dataclass
class FakeTwinPort:
    stores: LaboratoryArtifactStores
    operations = frozenset({"build_twin", "validate_policy"})
    evidence_kinds = frozenset({"twin_build", "twin_validation_report"})
    identity = RuntimeIdentity(
        PortAuthority.TWIN,
        202,
        "twin-service",
        "twin-model",
        "twin-state",
        "twin-config",
    )
    spec: WorldSpec | None = None

    def build(self, spec: WorldSpec) -> ArtifactRef:
        self.spec = spec
        return self.stores.agent.put(
            ArtifactEnvelope(
                kind="twin_build",
                payload={"world_spec_id": spec.spec_id, "world_instance_id": "twin-world"},
                provenance=spec.sources,
                validation_conditions=("Twin runtime identity is independent from Reality",),
            )
        )

    def execute(self, request: OperationRequest) -> OperationResult:
        if request.operation != "validate_policy" or self.spec is None:
            return OperationResult(request.request_id, "rejected", detail="Twin not built")
        arguments = request.arguments
        assert isinstance(arguments, dict)
        build_ref = _ref(arguments["twin_build_id"], "twin_build")
        self.stores.agent.get(build_ref)
        PhysicalBelief.model_validate(self.spec.document["model_parameters"]["physical_belief"])
        task = self.spec.document["task"]
        action = select_push_policy(tuple(task["target_position_m"]))
        protocol = load_default_protocol()
        reference = self.stores.agent.put(
            ArtifactEnvelope(
                kind="twin_validation_report",
                payload={
                    "goal_target_m": list(task["target_position_m"]),
                    "goal_target_radius_m": task["target_radius_m"],
                    "max_attempts": 1,
                    "protocol": protocol.model_dump(mode="json"),
                    "protocol_sha256": protocol_sha256(protocol),
                    "selected_action": action.model_dump(mode="json"),
                    "valid": True,
                },
                provenance=(build_ref,),
                validation_conditions=("selected action is feasible in Twin",),
            )
        )
        return OperationResult(request.request_id, "completed", (reference,))


@dataclass
class FakeEvaluatorPort:
    stores: LaboratoryArtifactStores
    operations = frozenset({"evaluate_terminal"})
    evidence_kinds = frozenset({"evaluation_report"})
    identity = RuntimeIdentity(
        PortAuthority.EVALUATOR,
        303,
        "evaluator-service",
        "evaluator-model",
        "evaluator-state",
        "evaluator-config",
    )

    def evaluate_terminal(self, terminal_artifact: ArtifactRef) -> ArtifactRef:
        deployment = self.stores.agent.get(terminal_artifact)
        assert deployment.kind == "reality_deployment"
        return self.stores.evaluator.put(
            ArtifactEnvelope(
                kind="evaluation_report",
                visibility=ArtifactVisibility.EVALUATOR,
                payload={"success": True, "terminal": True},
                provenance=(terminal_artifact,),
                validation_conditions=("terminal cube pose lies inside target region",),
            )
        )


def test_friction_capability_runs_end_to_end_with_content_lineage(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=FakeRealityPort(stores),
        twin=FakeTwinPort(stores),
        evaluator=FakeEvaluatorPort(stores),
    )

    result = capability.run()

    assert capability.pipeline.state is PipelineState.EVALUATED
    assert stores.evaluator.get(result.evaluation_report).payload["success"] is True
    patch = stores.agent.get(result.world_spec_patch)
    twin_build = stores.agent.get(result.twin_build)
    policy = stores.agent.get(result.policy_artifact)
    assert patch.payload["patch_id"].startswith("sha256:")
    assert patch.provenance == (result.parameter_posterior,)
    assert result.world_spec_patch in twin_build.provenance
    assert result.parameter_posterior not in twin_build.provenance
    assert policy.payload["protocol_sha256"] == protocol_sha256(load_default_protocol())
    assert tuple(item.current for item in capability.pipeline.history) == (
        PipelineState.CAPABILITY_GAP_ASSESSED,
        PipelineState.REALITY_EVIDENCE_CAPTURED,
        PipelineState.POSTERIOR_ESTIMATED,
        PipelineState.TWIN_BUILT,
        PipelineState.TWIN_VALIDATED,
        PipelineState.POLICY_VALIDATED,
        PipelineState.REALITY_DEPLOYED,
        PipelineState.EVALUATED,
    )


def test_nonfresh_reality_deployment_is_preserved_as_failure(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=FakeRealityPort(stores, fresh_deployment=False),
        twin=FakeTwinPort(stores),
        evaluator=FakeEvaluatorPort(stores),
    )

    with pytest.raises(CapabilityExecutionError, match="fresh Reality episode"):
        capability.run()

    assert capability.pipeline.state is PipelineState.FAILED


def test_open_capability_gap_fails_before_reality_execution(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    twin = FakeTwinPort(stores)
    twin.evidence_kinds = frozenset({"twin_build"})
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=FakeRealityPort(stores),
        twin=twin,
        evaluator=FakeEvaluatorPort(stores),
    )

    with pytest.raises(CapabilityExecutionError, match="gap remains open"):
        capability.run()

    assert capability.pipeline.state is PipelineState.FAILED


def test_port_crash_is_redacted_and_preserved_as_fail_artifact(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    reality = FakeRealityPort(stores)

    def crash(_request):  # noqa: ANN001, ANN202
        raise RuntimeError("PRIVATE-RUNTIME-SENTINEL")

    reality.execute = crash
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=reality,
        twin=FakeTwinPort(stores),
        evaluator=FakeEvaluatorPort(stores),
    )

    with pytest.raises(CapabilityExecutionError) as captured:
        capability.run()

    assert "PRIVATE-RUNTIME-SENTINEL" not in str(captured.value)
    assert capability.pipeline.state is PipelineState.FAILED
    failure_ref = capability.pipeline.history[-1].evidence[0]
    assert failure_ref.outcome is ArtifactOutcome.FAIL
    assert stores.agent.get(failure_ref).payload["errors"] == ["RuntimeError"]


@pytest.mark.parametrize("failure_mode", ["wrong_kind", "forged_ref"])
def test_invalid_port_evidence_terminalizes_with_fail_artifact(tmp_path, failure_mode: str) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    reality = FakeRealityPort(stores)

    def invalid(request: OperationRequest) -> OperationResult:
        if failure_mode == "wrong_kind":
            reference = stores.agent.put(
                ArtifactEnvelope(
                    kind="unrelated_note",
                    payload={},
                    validation_conditions=("note schema valid",),
                )
            )
        else:
            reference = ArtifactRef(
                "sha256:" + "0" * 64,
                "trajectory_dataset",
                ArtifactVisibility.AGENT,
                ArtifactOutcome.PASS,
            )
        return OperationResult(request.request_id, "completed", (reference,))

    reality.execute = invalid
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=reality,
        twin=FakeTwinPort(stores),
        evaluator=FakeEvaluatorPort(stores),
    )

    with pytest.raises(CapabilityExecutionError):
        capability.run()

    assert capability.pipeline.state is PipelineState.FAILED
    failure = stores.agent.get(capability.pipeline.history[-1].evidence[0])
    assert failure.outcome is ArtifactOutcome.FAIL


@pytest.mark.parametrize("failure_mode", ["forged_fail", "private_detail"])
def test_invalid_failed_result_is_redacted_and_replaced(tmp_path, failure_mode: str) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    reality = FakeRealityPort(stores)

    def invalid(request: OperationRequest) -> OperationResult:
        evidence = ()
        if failure_mode == "forged_fail":
            evidence = (
                ArtifactRef(
                    "sha256:" + "0" * 64,
                    "trajectory_dataset.validation_failure",
                    ArtifactVisibility.AGENT,
                    ArtifactOutcome.FAIL,
                ),
            )
        return OperationResult(
            request.request_id,
            "failed" if evidence else "rejected",
            evidence,
            detail="PRIVATE-REALITY-DETAIL-42",
        )

    reality.execute = invalid
    capability = FrictionCapabilityPipeline(
        stores=stores,
        reality=reality,
        twin=FakeTwinPort(stores),
        evaluator=FakeEvaluatorPort(stores),
    )

    with pytest.raises(CapabilityExecutionError) as captured:
        capability.run()

    assert "PRIVATE-REALITY-DETAIL-42" not in str(captured.value)
    assert capability.pipeline.state is PipelineState.FAILED
    generated = capability.pipeline.history[-1].evidence[0]
    assert generated.artifact_id != "sha256:" + "0" * 64
    assert stores.agent.get(generated).outcome is ArtifactOutcome.FAIL


def test_task_spec_rejects_protocol_snapshot_hash_mismatch() -> None:
    seed = default_world_spec_seed(
        initial_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        world_config={"dt_s": 1 / 240},
    )
    task = seed["task"]
    task["protocol"]["final_push"]["contact_speed_m_s"] = 0.09

    with pytest.raises(ValueError, match="protocol_sha256"):
        FrictionTaskSpec.model_validate(task)


def test_trajectory_dataset_rejects_hidden_or_unknown_reality_metadata() -> None:
    seed = default_world_spec_seed(
        initial_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        world_config={"dt_s": 1 / 240},
    )
    experiments = [item.model_dump(mode="json") for item in _experiments()]
    experiments[0]["metadata"]["hidden_parameters"] = {"mass_kg": 0.42}

    with pytest.raises(ValueError):
        TrajectoryDatasetPayload.model_validate(
            {
                **seed,
                "experiments": experiments,
                "runtime_process_id": 123,
                "world_instance_id": "reality-world",
            }
        )


def test_trajectory_dataset_protocol_matches_task_world_spec() -> None:
    seed = default_world_spec_seed(
        initial_position_m=(-0.12, 0.0, 0.025),
        target_position_m=(0.15, 0.0, 0.025),
        target_radius_m=0.06,
        world_config={"dt_s": 1 / 240},
    )
    experiments = [item.model_dump(mode="json") for item in _experiments()]
    experiments[1]["metadata"]["protocol_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="protocol identity"):
        TrajectoryDatasetPayload.model_validate(
            {
                **seed,
                "experiments": experiments,
                "runtime_process_id": 123,
                "world_instance_id": "reality-world",
            }
        )


def test_reality_deployment_rejects_hidden_or_unknown_fields() -> None:
    with pytest.raises(ValueError):
        RealityDeploymentPayload.model_validate(
            {
                "action_id": "push_straight_low",
                "attempt_count": 1,
                "attempts": [{"controller_outcome": "completed", "target_error_m": 0.01}],
                "controller_outcome": "completed",
                "fresh_episode": True,
                "hidden_parameters": {"mass_kg": 0.42},
                "observed_final_position_m": [0.14, 0.0, 0.025],
                "prediction_target_error_m": 0.01,
                "runtime_process_id": 101,
                "safety_tripped": False,
                "target_position_m": [0.15, 0.0, 0.025],
                "target_radius_m": 0.06,
                "terminal": True,
                "world_instance_id": "fresh-world",
            }
        )
