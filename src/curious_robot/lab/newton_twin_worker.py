"""Posterior-only Newton Twin worker entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from curious_robot.tasks import wrapped_yaw_error, yaw_from_quaternion_xyzw

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
from .friction_artifacts import (
    FrictionTaskSpec,
    PublicGeometrySpec,
    PublicNumericalConfig,
    candidate_push_policies,
)
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


def _candidate_result_payload(result: dict[str, Any], actions: tuple[Any, ...]) -> dict[str, Any]:
    """Serialize a completed or controller-rejected candidate without fabricating physics."""

    action = next(item for item in actions if item.action_id == result["candidate_id"])
    outcome = result["outcome"]
    common = {
        "candidate_id": result["candidate_id"],
        "world_instance_id": result["world_instance_id"],
        "scenario_id": result["scenario_id"],
        "goal_success": result["goal_success"],
        "lateral_contact_offset_m": action.lateral_contact_offset_m,
    }
    if outcome is None:
        return {
            **common,
            "feasible": False,
            "runtime_error": result["runtime_error"],
        }
    return {
        **outcome.model_dump(mode="json"),
        **common,
        "final_yaw_rad": result["final_yaw_rad"],
        "yaw_error_rad": result["yaw_error_rad"],
    }


def _twin_worker(connection, artifact_root: Path, device: str) -> None:  # noqa: ANN001
    import numpy as np

    from curious_robot.environment import BeliefEnvironment, rollout_candidate_in_world
    from curious_robot.schemas import PhysicalBelief
    from curious_robot.trajectory import CartesianTrackingError, IKUnreachableError

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
                    task = FrictionTaskSpec.model_validate(document["task"])
                    geometry = PublicGeometrySpec.model_validate(document["public_geometry"])
                    if (
                        geometry.object_archetype_id != task.object_id
                        or geometry.object_archetype_version != task.object_archetype_version
                        or geometry.geometry_hash != task.geometry_hash
                    ):
                        raise ValueError("Twin task and public geometry identities do not match")
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
                                "task_id": task.task_id,
                                "object_id": task.object_id,
                                "geometry_hash": task.geometry_hash,
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
                    requested_build = _reference(arguments["twin_build_id"], LabArtifactKind.TWIN_BUILD.value)
                    if requested_build != build_ref:
                        raise ValueError("validation requested for a different Twin build")
                    task = FrictionTaskSpec.model_validate(document["task"])
                    actions = candidate_push_policies(task)
                    candidate_results = []
                    rollout_actions = actions if task.task_id == "push-t-bar-v1" else (*actions, *actions)
                    for candidate_index, action in enumerate(rollout_actions):
                        candidate_world = world
                        owns_candidate_world = False
                        if task.task_id == "push-t-bar-v1":
                            candidate_world = BeliefEnvironment.from_belief(
                                belief,
                                config,
                                scenario_id=action.action_id,
                            )
                            owns_candidate_world = True
                        try:
                            try:
                                outcome = rollout_candidate_in_world(
                                    candidate_world,
                                    belief,
                                    action,
                                    config,
                                    task.protocol,
                                )
                            except (CartesianTrackingError, IKUnreachableError) as error:
                                candidate_results.append(
                                    {
                                        "candidate_id": action.action_id,
                                        "world_instance_id": str(candidate_world.world_instance_id),
                                        "scenario_id": action.action_id,
                                        "outcome": None,
                                        "runtime_error": type(error).__name__,
                                        "goal_success": False,
                                        "candidate_index": candidate_index,
                                    }
                                )
                                continue
                            observation = candidate_world.observe_object_pose()
                            final_yaw = yaw_from_quaternion_xyzw(observation.pose.orientation_xyzw)
                            yaw_error = wrapped_yaw_error(final_yaw, task.target_yaw_rad)
                            position_error = float(
                                np.linalg.norm(
                                    np.asarray(outcome.final_position_m[:2])
                                    - np.asarray(task.target_position_m[:2])
                                )
                            )
                            goal_success = position_error <= task.target_radius_m and (
                                task.yaw_tolerance_rad is None or yaw_error <= task.yaw_tolerance_rad
                            )
                            candidate_results.append(
                                {
                                    "candidate_id": action.action_id,
                                    "world_instance_id": str(outcome.world_instance_id),
                                    "scenario_id": outcome.scenario_id,
                                    "outcome": outcome,
                                    "final_yaw_rad": final_yaw,
                                    "yaw_error_rad": yaw_error,
                                    "goal_success": goal_success,
                                    "candidate_index": candidate_index,
                                }
                            )
                            if task.task_id == "push-cube-v1" and (
                                goal_success or not outcome.feasible
                            ):
                                break
                        finally:
                            if owns_candidate_world:
                                candidate_world.close()
                    feasible = [
                        item
                        for item in candidate_results
                        if item["outcome"] is not None
                        and item["outcome"].feasible
                        and item["goal_success"]
                    ]
                    selected = min(
                        feasible,
                        key=lambda item: (
                            max(
                                item["outcome"].target_error_m / task.target_radius_m,
                                item["yaw_error_rad"] / task.yaw_tolerance_rad
                                if task.yaw_tolerance_rad is not None
                                else 0.0,
                            ),
                            item["outcome"].peak_force_n,
                            item["candidate_id"],
                        ),
                        default=None,
                    )
                    outcomes = [
                        item["outcome"]
                        for item in candidate_results
                        if item["outcome"] is not None
                    ]
                    if selected is not None:
                        action = next(item for item in actions if item.action_id == selected["candidate_id"])
                        reference = store.put(
                            ArtifactEnvelope(
                                kind=LabArtifactKind.TWIN_VALIDATION_REPORT.value,
                                payload={
                                    "goal_target_m": list(task.target_position_m),
                                    "goal_target_radius_m": task.target_radius_m,
                                    "goal_target_yaw_rad": task.target_yaw_rad,
                                    "goal_yaw_tolerance_rad": task.yaw_tolerance_rad,
                                    "task_id": task.task_id,
                                    "task_version": task.task_version,
                                    "object_id": task.object_id,
                                    "object_archetype_version": task.object_archetype_version,
                                    "geometry_hash": task.geometry_hash,
                                    "max_attempts": 2 if task.task_id == "push-cube-v1" else 1,
                                    "outcome": selected["outcome"].model_dump(mode="json"),
                                    "outcomes": [
                                        _candidate_result_payload(item, actions)
                                        for item in candidate_results
                                    ],
                                    "protocol": task.protocol.model_dump(mode="json"),
                                    "protocol_sha256": task.protocol_sha256,
                                    "selected_action": action.model_dump(mode="json"),
                                    "task_success": True,
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
                                "outcomes": [
                                    _candidate_result_payload(item, actions)
                                    for item in candidate_results
                                ]
                            },
                            errors=("Twin policy rollout is infeasible or misses the target",),
                            provenance=(build_ref,),
                            diagnostics={
                                "feasible": bool(outcomes)
                                and all(item.feasible for item in outcomes),
                                "final_position_m": (
                                    list(outcomes[-1].final_position_m) if outcomes else None
                                ),
                                "target_error_m": (
                                    outcomes[-1].target_error_m if outcomes else None
                                ),
                                "target_radius_m": task.target_radius_m,
                                "candidates": [
                                    {
                                        "candidate_id": item["candidate_id"],
                                        "target_error_m": (
                                            item["outcome"].target_error_m
                                            if item["outcome"] is not None
                                            else None
                                        ),
                                        "yaw_error_rad": item.get("yaw_error_rad"),
                                        "feasible": bool(item["outcome"])
                                        and item["outcome"].feasible,
                                        "runtime_error": item.get("runtime_error"),
                                        "lateral_contact_offset_m": next(
                                            candidate.lateral_contact_offset_m
                                            for candidate in actions
                                            if candidate.action_id == item["candidate_id"]
                                        ),
                                    }
                                    for item in candidate_results
                                ],
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
    from curious_robot.world_config import WorldConfig, object_kind_from_archetype_id

    values = PublicNumericalConfig.model_validate(document["numerical_config"])
    task = FrictionTaskSpec.model_validate(document["task"])
    return WorldConfig(
        task_id=task.task_id,
        device=device,
        dt_s=values.dt_s,
        include_robot=values.include_robot,
        object_kind=object_kind_from_archetype_id(task.object_id),
        initial_position_m=values.initial_position_m,
        initial_yaw_rad=values.initial_yaw_rad,
        maximum_cartesian_speed_m_s=values.maximum_cartesian_speed_m_s,
        maximum_tool_force_n=values.maximum_tool_force_n,
        observation_noise_std=values.observation_noise_std,
        prediction_residual_tolerance_m=values.prediction_residual_tolerance_m,
        seed=values.seed + 10_000,
        target_position_m=values.target_position_m,
        target_radius_m=values.target_radius_m,
        target_yaw_rad=task.target_yaw_rad,
        target_yaw_tolerance_rad=task.yaw_tolerance_rad,
    )
