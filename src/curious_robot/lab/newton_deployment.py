"""Bounded policy and capability deployment inside the Reality authority."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from curious_robot.belief import initial_belief
from curious_robot.environment import TruthEnvironment
from curious_robot.ports import RobotRealityAdapter
from curious_robot.protocol import PhysicalExperimentProtocol, protocol_sha256
from curious_robot.schemas import ActionCandidate
from curious_robot.tools import RobotTools
from curious_robot.world_config import WorldConfig

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
    experiment_world_id: str | None,
    capability_sessions: dict[str, dict],
    device: str,
    seed: int,
) -> OperationResult:
    capability_ref = None
    capability_payload = None
    capability_session = None
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
        action = ActionCandidate.model_validate(policy.payload["action"])
        attempt_limit = int(policy.payload["max_attempts"])
        if not 1 <= attempt_limit <= 3:
            raise ValueError("policy max_attempts must be in [1, 3]")
        goal_target = np.asarray(policy.payload["goal_target_m"][:2], dtype=float)
        goal_radius = float(policy.payload["goal_target_radius_m"])
        protocol = PhysicalExperimentProtocol.model_validate(policy.payload["protocol"])
        if policy.payload["protocol_sha256"] != protocol_sha256(protocol):
            raise ValueError("policy protocol hash does not match its snapshot")
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
            protocol=protocol,
            device=device,
            seed=seed,
        )
        attempts = []
        peak_force = 0.0
        for _attempt in range(attempt_limit):
            execution = tools.execute_safe_push(
                action.target_m,
                action.max_force_n,
                speed_m_s=action.speed_m_s,
                push_height_m=action.push_height_m,
            )
            final_position = world.observe_object_pose().pose.position_m
            target_error = float(
                np.linalg.norm(np.asarray(final_position[:2]) - goal_target)
            )
            attempts.append(
                {
                    "controller_outcome": execution.outcome.value,
                    "target_error_m": target_error,
                }
            )
            peak_force = max(peak_force, execution.peak_contact_force_n)
            if (
                target_error <= goal_radius
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
                or len(capability_session["attempts"])
                >= capability_payload.recovery.maximum_attempts
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
            observed_final_position_m=final_position,
            peak_contact_force_n=peak_force,
            prediction_target_error_m=target_error,
            runtime_process_id=identity.process_id,
            safety_tripped=execution.safety_tripped,
            target_position_m=tuple(policy.payload["goal_target_m"]),
            target_radius_m=goal_radius,
            terminal=True,
            world_instance_id=world.world_instance_id,
        )
        reference = store.put(
            ArtifactEnvelope(
                kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
                payload=payload.model_dump(mode="json"),
                provenance=(
                    (policy_ref, capability_ref) if capability_ref is not None else (policy_ref,)
                ),
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
    protocol: PhysicalExperimentProtocol,
    device: str,
    seed: int,
):  # noqa: ANN202
    if session is not None and session.get("world") is not None:
        return session["world"], session["tools"], session["config"]
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
