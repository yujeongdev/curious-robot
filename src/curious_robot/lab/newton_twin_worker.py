"""Posterior-only Newton Twin worker entrypoint."""

from __future__ import annotations

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
    WorldSpec,
    content_id,
)
from .friction_artifacts import FrictionTaskSpec, PublicNumericalConfig, select_push_policy
from .ports import PortAuthority, RuntimeIdentity
from .process_port import send_error, send_ready, send_result


def _reference(
    artifact_id: str,
    kind: str,
    *,
    visibility: ArtifactVisibility = ArtifactVisibility.AGENT,
    outcome: ArtifactOutcome = ArtifactOutcome.PASS,
) -> ArtifactRef:
    return ArtifactRef(artifact_id, kind, visibility, outcome)

def _twin_worker(connection, artifact_root: Path, device: str) -> None:  # noqa: ANN001
    import numpy as np

    from curious_robot.environment import BeliefEnvironment, rollout_candidate_in_world
    from curious_robot.schemas import PhysicalBelief

    store = open_agent_artifact_capability(
        artifact_root,
        read_kinds=frozenset(),
        write_kinds=frozenset(
            {
                LabArtifactKind.TWIN_BUILD.value,
                LabArtifactKind.TWIN_VALIDATION_REPORT.value,
            }
        ),
    )
    service_nonce = str(uuid4())
    identity = RuntimeIdentity.for_current_process(
        authority=PortAuthority.TWIN,
        world_id=f"twin-service:{service_nonce}",
        model_id=f"twin-model-owner:{service_nonce}",
        state_id=f"twin-state-owner:{service_nonce}",
        config_id=content_id({"authority": "twin", "nonce": service_nonce}),
    )
    send_ready(connection, identity)
    world = None
    belief = None
    config = None
    build_ref = None
    try:
        while True:
            message = connection.recv()
            command = message.get("command")
            if command == "shutdown":
                return
            try:
                if command == "build" and isinstance(message.get("payload"), WorldSpec):
                    if world is not None:
                        world.close()
                    spec = message["payload"]
                    document = spec.document
                    belief = PhysicalBelief.model_validate(document["model_parameters"]["physical_belief"])
                    config = _world_config_from_spec(document, device=device)
                    world = BeliefEnvironment.from_belief(
                        belief,
                        config,
                        scenario_id="friction-policy-validation",
                    )
                    build_ref = store.put(
                        ArtifactEnvelope(
                            kind=LabArtifactKind.TWIN_BUILD.value,
                            payload={
                                "runtime_process_id": identity.process_id,
                                "world_config_id": world.world_config_id,
                                "world_instance_id": world.world_instance_id,
                                "world_spec_id": spec.spec_id,
                            },
                            provenance=spec.sources,
                            validation_conditions=(
                                "Twin parameters originate only from the posterior WorldSpec",
                                "Twin runtime identity is independent from Reality",
                            ),
                        )
                    )
                    send_result(connection, build_ref)
                elif command == "execute" and isinstance(message.get("payload"), OperationRequest):
                    request = message["payload"]
                    if request.operation != LabOperation.VALIDATE_POLICY.value or any(
                        value is None for value in (world, belief, config, build_ref)
                    ):
                        raise RuntimeError("Twin must be built before policy validation")
                    arguments = request.arguments
                    if not isinstance(arguments, dict):
                        raise TypeError("Twin operation arguments must be an object")
                    requested_build = _reference(
                        arguments["twin_build_id"], LabArtifactKind.TWIN_BUILD.value
                    )
                    if requested_build != build_ref:
                        raise ValueError("validation requested for a different Twin build")
                    task = FrictionTaskSpec.model_validate(document["task"])
                    action = select_push_policy(task.target_position_m)
                    outcomes = []
                    task_error = float("inf")
                    for _attempt in range(2):
                        outcome = rollout_candidate_in_world(
                            world,
                            belief,
                            action,
                            config,
                            task.protocol,
                        )
                        outcomes.append(outcome)
                        task_error = float(
                            np.linalg.norm(
                                np.asarray(outcome.final_position_m[:2])
                                - np.asarray(task.target_position_m[:2])
                            )
                        )
                        if task_error <= task.target_radius_m or not outcome.feasible:
                            break
                    task_success = task_error <= task.target_radius_m
                    if all(item.feasible for item in outcomes) and task_success:
                        reference = store.put(
                            ArtifactEnvelope(
                                kind=LabArtifactKind.TWIN_VALIDATION_REPORT.value,
                                payload={
                                    "goal_target_m": list(task.target_position_m),
                                    "goal_target_radius_m": task.target_radius_m,
                                    "max_attempts": 2,
                                    "outcome": outcomes[-1].model_dump(mode="json"),
                                    "outcomes": [item.model_dump(mode="json") for item in outcomes],
                                    "protocol": task.protocol.model_dump(mode="json"),
                                    "protocol_sha256": task.protocol_sha256,
                                    "selected_action": action.model_dump(mode="json"),
                                    "task_success": task_success,
                                    "valid": True,
                                },
                                provenance=(build_ref,),
                                validation_conditions=(
                                    "policy rollout remains within force and tilt bounds",
                                    "policy reaches the task target radius in Twin",
                                    "policy rollout completes in the built Twin world",
                                ),
                            )
                        )
                        result = OperationResult(request.request_id, "completed", (reference,))
                    else:
                        reference = store.record_validation_failure(
                            attempted_kind=LabArtifactKind.TWIN_VALIDATION_REPORT.value,
                            rejected_payload={
                                "outcomes": [item.model_dump(mode="json") for item in outcomes]
                            },
                            errors=("Twin policy rollout is infeasible or misses the target",),
                            provenance=(build_ref,),
                            diagnostics={
                                "feasible": all(item.feasible for item in outcomes),
                                "final_position_m": list(outcomes[-1].final_position_m),
                                "target_error_m": task_error,
                                "target_radius_m": task.target_radius_m,
                            },
                            validation_conditions=("policy rollout remains within force and tilt bounds",),
                        )
                        result = OperationResult(request.request_id, "failed", (reference,))
                    send_result(connection, result)
                else:
                    raise ValueError("unsupported Twin RPC command")
            except BaseException as error:
                send_error(connection, error)
    finally:
        if world is not None:
            world.close()


def _world_config_from_spec(document: dict[str, Any], *, device: str):  # noqa: ANN202
    from curious_robot.world_config import WorldConfig

    values = PublicNumericalConfig.model_validate(document["numerical_config"])
    return WorldConfig(
        device=device,
        dt_s=values.dt_s,
        include_robot=values.include_robot,
        initial_position_m=values.initial_position_m,
        maximum_cartesian_speed_m_s=values.maximum_cartesian_speed_m_s,
        maximum_tool_force_n=values.maximum_tool_force_n,
        observation_noise_std=values.observation_noise_std,
        prediction_residual_tolerance_m=values.prediction_residual_tolerance_m,
        seed=values.seed + 10_000,
        target_position_m=values.target_position_m,
        target_radius_m=values.target_radius_m,
    )
