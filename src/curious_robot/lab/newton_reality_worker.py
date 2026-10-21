"""Hidden-truth Newton Reality worker entrypoint."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_store import open_agent_artifact_capability
from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
    OperationResult,
    content_id,
)
from .friction_artifacts import (
    RealityDeploymentPayload,
    TrajectoryDatasetPayload,
    default_world_spec_seed,
)
from .ports import PortAuthority, RuntimeIdentity
from .process_port import send_error, send_ready, send_result

_PRIVATE_SCENARIO_SEED_ENV = "CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED"


def _reference(
    artifact_id: str,
    kind: str,
    *,
    visibility: ArtifactVisibility = ArtifactVisibility.AGENT,
    outcome: ArtifactOutcome = ArtifactOutcome.PASS,
) -> ArtifactRef:
    return ArtifactRef(artifact_id, kind, visibility, outcome)

def _reality_worker(  # noqa: ANN001
    connection,
    artifact_root: Path,
    device: str,
    seed: int,
    private_seed_override: str | None,
) -> None:
    """Own hidden truth for both experiment and a newly allocated deployment world."""

    from dataclasses import replace

    import numpy as np

    from curious_robot.belief import initial_belief
    from curious_robot.environment import TruthEnvironment
    from curious_robot.ports import RobotRealityAdapter
    from curious_robot.protocol import PhysicalExperimentProtocol, protocol_sha256
    from curious_robot.schemas import ActionCandidate
    from curious_robot.surface import sample_surface_profile
    from curious_robot.tools import RobotTools
    from curious_robot.world_config import PhysicalParameters, WorldConfig

    store = open_agent_artifact_capability(
        artifact_root,
        read_kinds=frozenset(
            {LabArtifactKind.CAPABILITY_GAP.value, LabArtifactKind.POLICY_ARTIFACT.value}
        ),
        write_kinds=frozenset(
            {
                LabArtifactKind.TRAJECTORY_DATASET.value,
                LabArtifactKind.REALITY_DEPLOYMENT.value,
            }
        ),
    )
    scenario_seed = (
        int(private_seed_override, 10) if private_seed_override is not None else secrets.randbits(63)
    )
    if not 0 <= scenario_seed < 2**63:
        raise ValueError(f"{_PRIVATE_SCENARIO_SEED_ENV} must be in [0, 2**63)")
    scenario_rng = np.random.default_rng(scenario_seed)
    surface = sample_surface_profile(scenario_seed + 1_000)
    hidden_parameters = PhysicalParameters(
        mass_kg=float(scenario_rng.uniform(0.08, 0.45)),
        static_friction=surface.effective_static_friction,
        dynamic_friction=surface.effective_dynamic_friction,
        restitution=surface.restitution,
    )
    hidden_initial_position = (
        float(scenario_rng.uniform(-0.16, -0.10)),
        float(scenario_rng.uniform(-0.03, 0.03)),
        0.025,
    )
    service_nonce = str(uuid4())
    identity = RuntimeIdentity.for_current_process(
        authority=PortAuthority.REALITY,
        world_id=f"reality-service:{service_nonce}",
        model_id=f"reality-model-owner:{service_nonce}",
        state_id=f"reality-state-owner:{service_nonce}",
        config_id=content_id({"authority": "reality", "nonce": service_nonce}),
    )
    send_ready(connection, identity)
    experiment_world_id: str | None = None

    while True:
        message = connection.recv()
        command = message.get("command")
        if command == "shutdown":
            return
        try:
            if command != "execute" or not isinstance(message.get("payload"), OperationRequest):
                raise ValueError("unsupported Reality RPC command")
            request = message["payload"]
            arguments = request.arguments
            if not isinstance(arguments, dict):
                raise TypeError("Reality operation arguments must be an object")
            if request.operation == LabOperation.FRICTION_EXPERIMENT.value:
                capability_ref = _reference(
                    arguments["provenance"][0], LabArtifactKind.CAPABILITY_GAP.value
                )
                store.get(capability_ref)
                config = WorldConfig(
                    device=device,
                    initial_position_m=hidden_initial_position,
                    include_robot=True,
                    seed=seed,
                )
                world = TruthEnvironment(
                    hidden_parameters,
                    config,
                    scene="truth",
                    scenario_id="friction-experiment",
                )
                try:
                    world.settle()
                    observed_initial = world.observe_object_pose().pose.position_m
                    public_config = replace(config, initial_position_m=observed_initial)
                    tools = RobotTools(
                        RobotRealityAdapter(world),
                        initial_belief(),
                        public_config,
                    )
                    static_evidence = tools.probe_static_friction()
                    dynamic_evidence = tools.probe_dynamic_friction()
                    experiment_world_id = world.world_instance_id
                    spec_seed = default_world_spec_seed(
                        initial_position_m=observed_initial,
                        target_position_m=config.target_position_m,
                        target_radius_m=config.target_radius_m,
                        world_config=_public_world_config(public_config),
                        protocol=tools.protocol,
                    )
                    dataset_payload = TrajectoryDatasetPayload.model_validate(
                        {
                            **spec_seed,
                            "experiments": [
                                static_evidence.model_dump(mode="json"),
                                dynamic_evidence.model_dump(mode="json"),
                            ],
                            "world_instance_id": experiment_world_id,
                            "runtime_process_id": identity.process_id,
                        }
                    ).model_dump(mode="json")
                    fatal_flags = {
                        "insufficient_native_contact_samples",
                        "insufficient_contact_coverage",
                    }.intersection(
                        (*static_evidence.quality_flags, *dynamic_evidence.quality_flags)
                    )
                    if fatal_flags:
                        reference = store.record_validation_failure(
                            attempted_kind=LabArtifactKind.TRAJECTORY_DATASET.value,
                            rejected_payload=dataset_payload,
                            errors=tuple(sorted(fatal_flags)),
                            provenance=(capability_ref,),
                            validation_conditions=(
                                "experiment contains the protocol minimum contact coverage",
                            ),
                        )
                        result = OperationResult(request.request_id, "failed", (reference,))
                    else:
                        reference = store.put(
                            ArtifactEnvelope(
                                kind=LabArtifactKind.TRAJECTORY_DATASET.value,
                                payload=dataset_payload,
                                provenance=(capability_ref,),
                                validation_conditions=(
                                    "samples come from native robot-tool to cube contacts",
                                    "experiment contains the protocol minimum contact coverage",
                                ),
                            )
                        )
                        result = OperationResult(request.request_id, "completed", (reference,))
                finally:
                    world.close()
            elif request.operation == LabOperation.DEPLOY_POLICY.value:
                if experiment_world_id is None:
                    raise RuntimeError("friction experiment must precede deployment")
                policy_ref = _reference(
                    arguments["policy_artifact_id"], LabArtifactKind.POLICY_ARTIFACT.value
                )
                policy = store.get(policy_ref)
                policy_payload = policy.payload
                if not isinstance(policy_payload, dict):
                    raise TypeError("policy payload must be an object")
                action = ActionCandidate.model_validate(policy_payload["action"])
                max_attempts = int(policy_payload["max_attempts"])
                if not 1 <= max_attempts <= 3:
                    raise ValueError("policy max_attempts must be in [1, 3]")
                goal_target = np.asarray(policy_payload["goal_target_m"][:2], dtype=float)
                goal_radius = float(policy_payload["goal_target_radius_m"])
                protocol = PhysicalExperimentProtocol.model_validate(policy_payload["protocol"])
                if policy_payload["protocol_sha256"] != protocol_sha256(protocol):
                    raise ValueError("policy protocol hash does not match its snapshot")
                config = WorldConfig(
                    device=device,
                    initial_position_m=hidden_initial_position,
                    include_robot=True,
                    seed=seed + 1,
                )
                world = TruthEnvironment(
                    hidden_parameters,
                    config,
                    scene="truth",
                    scenario_id="fresh-policy-deployment",
                )
                try:
                    world.settle()
                    observed_initial = world.observe_object_pose().pose.position_m
                    tools = RobotTools(
                        RobotRealityAdapter(world),
                        initial_belief(),
                        replace(config, initial_position_m=observed_initial),
                        protocol=protocol,
                    )
                    attempts = []
                    for _attempt in range(max_attempts):
                        execution = tools.execute_safe_push(
                            action.target_m,
                            action.max_force_n,
                            speed_m_s=action.speed_m_s,
                            push_height_m=action.push_height_m,
                        )
                        observation = world.observe_object_pose()
                        final_position = observation.pose.position_m
                        distance_xy = float(
                            np.linalg.norm(np.asarray(final_position[:2]) - goal_target)
                        )
                        attempts.append(
                            {
                                "controller_outcome": execution.outcome.value,
                                "target_error_m": distance_xy,
                            }
                        )
                        if distance_xy <= goal_radius or execution.safety_tripped:
                            break
                    deployment_payload = RealityDeploymentPayload.model_validate(
                        {
                            "action_id": action.action_id,
                            "attempt_count": len(attempts),
                            "attempts": attempts,
                            "controller_outcome": execution.outcome.value,
                            "fresh_episode": world.world_instance_id != experiment_world_id,
                            "observed_final_position_m": list(final_position),
                            "prediction_target_error_m": distance_xy,
                            "runtime_process_id": identity.process_id,
                            "safety_tripped": execution.safety_tripped,
                            "target_position_m": list(policy_payload["goal_target_m"]),
                            "target_radius_m": goal_radius,
                            "terminal": True,
                            "world_instance_id": world.world_instance_id,
                        }
                    )
                    reference = store.put(
                        ArtifactEnvelope(
                            kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
                            payload=deployment_payload.model_dump(mode="json"),
                            provenance=(policy_ref,),
                            validation_conditions=(
                                "deployment uses a newly allocated Reality world",
                                "independent force guardrail outcome is recorded",
                            ),
                        )
                    )
                    result = OperationResult(request.request_id, "completed", (reference,))
                finally:
                    world.close()
            else:
                result = OperationResult(
                    request.request_id,
                    "rejected",
                    detail="unsupported Reality operation",
                )
            send_result(connection, result)
        except BaseException as error:  # worker boundary must report and remain diagnosable
            send_error(connection, error)


def _public_world_config(config) -> dict[str, Any]:  # noqa: ANN001
    return {
        "dt_s": config.dt_s,
        "include_robot": config.include_robot,
        "initial_position_m": list(config.initial_position_m),
        "maximum_cartesian_speed_m_s": config.maximum_cartesian_speed_m_s,
        "maximum_tool_force_n": config.maximum_tool_force_n,
        "prediction_residual_tolerance_m": config.prediction_residual_tolerance_m,
        "seed": config.seed,
        "target_position_m": list(config.target_position_m),
        "target_radius_m": config.target_radius_m,
    }
