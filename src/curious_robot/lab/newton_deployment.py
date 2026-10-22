"""Bounded policy and capability deployment inside the Reality authority."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from curious_robot.belief import initial_belief
from curious_robot.environment import TruthEnvironment
from curious_robot.objects import get_object_archetype
from curious_robot.planning import candidate_actions
from curious_robot.ports import RobotRealityAdapter
from curious_robot.protocol import PhysicalExperimentProtocol, protocol_sha256
from curious_robot.schemas import ActionCandidate
from curious_robot.tasks import get_task_preset, wrapped_yaw_error, yaw_from_quaternion_xyzw
from curious_robot.tools import RobotTools
from curious_robot.world_config import WorldConfig, object_kind_from_archetype_id

from .artifact_store import ScopedArtifactStore
from .capability_artifact import validate_capability_invocation
from .capability_skill import DeterministicFrictionSkill
from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
    OperationResult,
)
from .friction_artifacts import RealityDeploymentPayload
from .ports import RuntimeIdentity


def execute_deployment(
    *,
    request: OperationRequest,
    arguments: dict[str, Any],
    store: ScopedArtifactStore,
    identity: RuntimeIdentity,
    hidden_parameters,  # noqa: ANN001
    hidden_initial_position: tuple[float, float, float],
    hidden_initial_yaw_rad: float,
    experiment_world_id: str | None,
    capability_sessions: dict[str, dict],
    device: str,
    seed: int,
) -> OperationResult:
    capability_ref = None
    capability_payload = None
    capability_session = None
    world = None
    if request.operation == LabOperation.DEPLOY_POLICY.value and experiment_world_id is None:
        raise RuntimeError("friction experiment must precede deployment")
    if request.operation == LabOperation.DEPLOY_CAPABILITY.value:
        capability_ref = _reference(
            arguments["capability_artifact_id"],
            LabArtifactKind.CAPABILITY_ARTIFACT.value,
        )
        capability_payload = DeterministicFrictionSkill.load(store, capability_ref).payload
        invocation = validate_capability_invocation(
            capability_payload,
            capability_ref,
            arguments,
        )
        session_key = f"{capability_ref.artifact_id}:{invocation.invocation_id}"
        capability_session = capability_sessions.setdefault(
            session_key,
            {"attempts": [], "closed": False, "peak_contact_force_n": 0.0},
        )
        expected_attempt = len(capability_session["attempts"]) + 1
        if capability_session["closed"] or invocation.attempt_index != expected_attempt:
            raise ValueError("capability attempt index was replayed or reset")

    try:
        policy_ref = _reference(
            arguments["policy_artifact_id"],
            LabArtifactKind.POLICY_ARTIFACT.value,
        )
        policy = store.get(policy_ref)
        if not isinstance(policy.payload, dict):
            raise TypeError("policy payload must be an object")
        if (
            len(policy.provenance) != 1
            or policy.provenance[0].kind != LabArtifactKind.TWIN_VALIDATION_REPORT.value
            or policy.provenance[0].outcome is not ArtifactOutcome.PASS
        ):
            raise ValueError("policy must directly descend from one Twin validation report")
        validation = store.get(policy.provenance[0])
        if not isinstance(validation.payload, dict):
            raise TypeError("Twin validation payload must be an object")
        action = ActionCandidate.model_validate(policy.payload["action"])
        task_id = str(policy.payload.get("task_id", "push-cube-v1"))
        object_id = str(policy.payload.get("object_id", "cube"))
        task_version = str(policy.payload.get("task_version", "1.0.0"))
        object_archetype_version = str(
            policy.payload.get("object_archetype_version", "1.0.0")
        )
        geometry_hash = policy.payload.get("geometry_hash")
        goal_target = np.asarray(policy.payload["goal_target_m"][:2], dtype=float)
        goal_radius = float(policy.payload["goal_target_radius_m"])
        target_yaw = float(policy.payload.get("goal_target_yaw_rad", 0.0))
        yaw_tolerance = policy.payload.get("goal_yaw_tolerance_rad")
        yaw_tolerance = float(yaw_tolerance) if yaw_tolerance is not None else None
        preset = get_task_preset(task_id)
        archetype = get_object_archetype(
            preset.object_archetype_id,
            preset.object_archetype_version,
        )
        geometry_matches = geometry_hash == archetype.geometry_hash or (
            task_id == "push-cube-v1" and geometry_hash is None
        )
        if (
            task_version != preset.version
            or object_id != preset.object_archetype_id
            or object_archetype_version != preset.object_archetype_version
            or not geometry_matches
            or tuple(policy.payload["goal_target_m"][:2])
            != (preset.goal.target.x_m, preset.goal.target.y_m)
            or goal_radius != preset.goal.position_tolerance_m
            or target_yaw != preset.goal.target.yaw_rad
            or yaw_tolerance != preset.goal.yaw_tolerance_rad
        ):
            raise ValueError("policy task, public geometry, or goal does not match its allowlisted preset")
        admitted_actions = candidate_actions(
            tuple(policy.payload["goal_target_m"]),
            task_id=task_id,
        )
        if action not in admitted_actions or validation.payload.get("selected_action") != action.model_dump(
            mode="json"
        ):
            raise ValueError("policy action was not the allowlisted action selected by Twin validation")
        if capability_payload is not None and object_id != "cube":
            raise ValueError("deterministic capability deployment supports cube geometry only")
        attempt_limit = int(policy.payload["max_attempts"])
        if not 1 <= attempt_limit <= 3:
            raise ValueError("policy max_attempts must be in [1, 3]")
        protocol = PhysicalExperimentProtocol.model_validate(policy.payload["protocol"])
        if policy.payload["protocol_sha256"] != protocol_sha256(protocol):
            raise ValueError("policy protocol hash does not match its snapshot")
        validated_fields = {
            "task_id": "task_id",
            "task_version": "task_version",
            "object_id": "object_id",
            "object_archetype_version": "object_archetype_version",
            "geometry_hash": "geometry_hash",
            "goal_target_m": "goal_target_m",
            "goal_target_radius_m": "goal_target_radius_m",
            "goal_target_yaw_rad": "goal_target_yaw_rad",
            "goal_yaw_tolerance_rad": "goal_yaw_tolerance_rad",
            "max_attempts": "max_attempts",
            "protocol": "protocol",
            "protocol_sha256": "protocol_sha256",
        }
        legacy_validation_defaults = {
            "task_id": "push-cube-v1",
            "task_version": "1.0.0",
            "object_id": "cube",
            "object_archetype_version": "1.0.0",
            "geometry_hash": None,
            "goal_target_yaw_rad": 0.0,
            "goal_yaw_tolerance_rad": None,
        }
        if (
            validation.payload.get("valid") is not True
            or validation.payload.get("task_success") is not True
            or any(
                policy.payload.get(policy_key) != validation.payload.get(validation_key)
                if validation_key not in legacy_validation_defaults
                else policy.payload.get(policy_key)
                != validation.payload.get(
                    validation_key,
                    legacy_validation_defaults[validation_key],
                )
                for policy_key, validation_key in validated_fields.items()
            )
        ):
            raise ValueError("policy fields do not match the cited PASS Twin validation")
        if capability_payload is not None:
            if (
                action != capability_payload.action_template
                or action.max_force_n > capability_payload.safety.maximum_force_n
                or action.speed_m_s > capability_payload.safety.maximum_speed_m_s
                or protocol != capability_payload.protocol
            ):
                raise ValueError("policy exceeds the capability guardrail")
            attempt_limit = 1

        world, tools, config = _world_and_tools(
            session=capability_session,
            capability_payload=capability_payload,
            hidden_parameters=hidden_parameters,
            hidden_initial_position=hidden_initial_position,
            hidden_initial_yaw_rad=hidden_initial_yaw_rad,
            protocol=protocol,
            device=device,
            seed=seed,
            object_id=object_id,
            task_id=task_id,
            target_position_m=tuple(policy.payload["goal_target_m"]),
            target_radius_m=goal_radius,
            target_yaw_rad=target_yaw,
            target_yaw_tolerance_rad=yaw_tolerance,
        )
        attempts = []
        peak_force = 0.0
        for _attempt in range(attempt_limit):
            execution = tools.execute_safe_push(
                action.target_m,
                action.max_force_n,
                speed_m_s=action.speed_m_s,
                push_height_m=action.push_height_m,
                lateral_contact_offset_m=action.lateral_contact_offset_m,
                contact_surface_id=action.contact_surface_id,
            )
            final_observation = world.observe_object_pose()
            final_position = final_observation.pose.position_m
            final_yaw = yaw_from_quaternion_xyzw(final_observation.pose.orientation_xyzw)
            target_error = float(np.linalg.norm(np.asarray(final_position[:2]) - goal_target))
            yaw_error = wrapped_yaw_error(final_yaw, target_yaw)
            attempts.append(
                {
                    "controller_outcome": execution.outcome.value,
                    "target_error_m": target_error,
                    "yaw_error_rad": yaw_error if yaw_tolerance is not None else None,
                }
            )
            peak_force = max(peak_force, execution.peak_contact_force_n)
            if (
                (target_error <= goal_radius and (yaw_tolerance is None or yaw_error <= yaw_tolerance))
                or execution.safety_tripped
                or execution.outcome.value != "completed"
            ):
                break

        if capability_session is not None and capability_payload is not None:
            capability_session["attempts"].extend(attempts)
            capability_session["peak_contact_force_n"] = max(
                capability_session["peak_contact_force_n"], peak_force
            )
            capability_session["closed"] = (
                target_error <= goal_radius
                or execution.safety_tripped
                or execution.outcome.value != "completed"
                or len(capability_session["attempts"]) >= capability_payload.recovery.maximum_attempts
            )
            attempts = capability_session["attempts"]
            peak_force = capability_session["peak_contact_force_n"]

        payload = RealityDeploymentPayload(
            action_id=action.action_id,
            attempt_count=len(attempts),
            attempts=attempts,
            controller_outcome=execution.outcome.value,
            request_id=request.request_id,
            invocation_id=(invocation.invocation_id if capability_payload is not None else None),
            attempt_index=(invocation.attempt_index if capability_payload is not None else 1),
            fresh_episode=world.world_instance_id != experiment_world_id,
            frame_id="simulation_world",
            object_id=object_id,
            task_id=task_id,
            task_version=task_version,
            object_archetype_version=object_archetype_version,
            geometry_hash=geometry_hash,
            observed_final_position_m=final_position,
            observed_final_yaw_rad=final_yaw,
            peak_contact_force_n=peak_force,
            prediction_target_error_m=target_error,
            runtime_process_id=identity.process_id,
            safety_tripped=execution.safety_tripped,
            target_position_m=tuple(policy.payload["goal_target_m"]),
            target_radius_m=goal_radius,
            target_yaw_rad=target_yaw,
            yaw_tolerance_rad=yaw_tolerance,
            terminal=True,
            world_instance_id=world.world_instance_id,
        )
        reference = store.put(
            ArtifactEnvelope(
                kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
                payload=payload.model_dump(mode="json"),
                provenance=((policy_ref, capability_ref) if capability_ref is not None else (policy_ref,)),
                validation_conditions=(
                    "deployment uses a newly allocated Reality world",
                    "independent force guardrail outcome is recorded",
                ),
            )
        )
        if capability_session is None or capability_session["closed"]:
            _close_session_world(capability_session, world)
        return OperationResult(request.request_id, "completed", (reference,))
    except BaseException:
        if capability_session is not None:
            capability_session["closed"] = True
            _close_session_world(capability_session, capability_session.get("world"))
        elif world is not None:
            _close_session_world(None, world)
        raise


def close_capability_sessions(sessions: dict[str, dict]) -> None:
    for session in sessions.values():
        _close_session_world(session, session.get("world"))


def _world_and_tools(
    *,
    session: dict | None,
    capability_payload,  # noqa: ANN001
    hidden_parameters,  # noqa: ANN001
    hidden_initial_position: tuple[float, float, float],
    hidden_initial_yaw_rad: float,
    protocol: PhysicalExperimentProtocol,
    device: str,
    seed: int,
    object_id: str,
    task_id: str,
    target_position_m: tuple[float, float, float],
    target_radius_m: float,
    target_yaw_rad: float,
    target_yaw_tolerance_rad: float | None,
):  # noqa: ANN202
    if session is not None and session.get("world") is not None:
        return session["world"], session["tools"], session["config"]
    config = WorldConfig(
        task_id=task_id,
        device=device,
        initial_position_m=hidden_initial_position,
        initial_yaw_rad=hidden_initial_yaw_rad,
        target_position_m=target_position_m,
        target_radius_m=target_radius_m,
        target_yaw_rad=target_yaw_rad,
        target_yaw_tolerance_rad=target_yaw_tolerance_rad,
        include_robot=True,
        object_kind=object_kind_from_archetype_id(object_id),
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
        if capability_payload is not None and (
            math.dist(
                observed_initial,
                capability_payload.applicability.validated_initial_position_m,
            )
            > capability_payload.applicability.initial_position_tolerance_m
        ):
            raise ValueError("Reality initial pose is outside capability validation")
        tools = RobotTools(
            RobotRealityAdapter(world),
            initial_belief(),
            replace(config, initial_position_m=observed_initial),
            protocol=protocol,
        )
        if session is not None:
            session.update({"world": world, "tools": tools, "config": config})
        return world, tools, config
    except BaseException:
        world.close()
        raise


def _close_session_world(session: dict | None, world) -> None:  # noqa: ANN001
    if world is not None:
        world.close()
    if session is not None:
        session["world"] = None


def _reference(
    artifact_id: str,
    kind: str,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id,
        kind,
        ArtifactVisibility.AGENT,
        ArtifactOutcome.PASS,
    )
