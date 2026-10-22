"""Terminal-only Evaluator worker entrypoint."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from curious_robot.objects import get_object_archetype, registered_planar_corner_error
from curious_robot.tasks import wrapped_yaw_error

from .artifact_store import open_agent_artifact_capability, open_evaluator_artifact_capability
from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    LabArtifactKind,
    LabOperation,
    content_id,
)
from .friction_artifacts import RealityDeploymentPayload
from .ports import PortAuthority, RuntimeIdentity
from .process_port import send_error, send_ready, send_result


def _evaluator_worker(connection, artifact_root: Path) -> None:  # noqa: ANN001
    import numpy as np

    agent_store = open_agent_artifact_capability(
        artifact_root,
        read_kinds=frozenset({LabArtifactKind.REALITY_DEPLOYMENT.value}),
        write_kinds=frozenset(),
    )
    evaluator_store = open_evaluator_artifact_capability(
        artifact_root,
        read_kinds=frozenset(),
        write_kinds=frozenset({LabArtifactKind.EVALUATION_REPORT.value}),
    )
    service_nonce = str(uuid4())
    identity = RuntimeIdentity.for_current_process(
        authority=PortAuthority.EVALUATOR,
        world_id=f"evaluator-service:{service_nonce}",
        model_id=f"evaluator-model:{service_nonce}",
        state_id=f"evaluator-state:{service_nonce}",
        config_id=content_id({"authority": "evaluator", "nonce": service_nonce}),
    )
    send_ready(connection, identity)
    while True:
        message = connection.recv()
        command = message.get("command")
        if command == "shutdown":
            return
        try:
            if command != LabOperation.EVALUATE_TERMINAL.value or not isinstance(
                message.get("payload"), ArtifactRef
            ):
                raise ValueError("unsupported Evaluator RPC command")
            deployment_ref = message["payload"]
            if (
                deployment_ref.kind != LabArtifactKind.REALITY_DEPLOYMENT.value
                or deployment_ref.visibility is not ArtifactVisibility.AGENT
                or deployment_ref.outcome is not ArtifactOutcome.PASS
            ):
                raise ValueError("Evaluator accepts only an agent PASS reality_deployment")
            deployment = agent_store.get(deployment_ref)
            payload = RealityDeploymentPayload.model_validate(deployment.payload)
            if not payload.fresh_episode:
                raise ValueError("Evaluator requires a terminal fresh Reality deployment")
            final = np.asarray(payload.observed_final_position_m[:2], dtype=float)
            target = np.asarray(payload.target_position_m[:2], dtype=float)
            target_radius = payload.target_radius_m
            target_error = float(np.linalg.norm(final - target))
            yaw_error = wrapped_yaw_error(
                payload.observed_final_yaw_rad,
                payload.target_yaw_rad,
            )
            yaw_within_tolerance = (
                True
                if payload.yaw_tolerance_rad is None
                else yaw_error <= payload.yaw_tolerance_rad
            )
            shape_error = registered_planar_corner_error(
                get_object_archetype(
                    payload.object_id,
                    payload.object_archetype_version,
                ),
                observed_xy_yaw=(
                    payload.observed_final_position_m[0],
                    payload.observed_final_position_m[1],
                    payload.observed_final_yaw_rad,
                ),
                target_xy_yaw=(
                    payload.target_position_m[0],
                    payload.target_position_m[1],
                    payload.target_yaw_rad,
                ),
            )
            controller_completed = payload.controller_outcome == "completed"
            success = (
                target_error <= target_radius
                and yaw_within_tolerance
                and not payload.safety_tripped
                and controller_completed
            )
            target_predicate = (
                "t_bar_in_target_pose" if payload.object_id == "t-bar" else "cube_in_target"
            )
            reference = evaluator_store.put(
                ArtifactEnvelope(
                    kind=LabArtifactKind.EVALUATION_REPORT.value,
                    visibility=ArtifactVisibility.EVALUATOR,
                    payload={
                        "goal_predicates": {
                            target_predicate: target_error <= target_radius and yaw_within_tolerance,
                            "position_within_tolerance": target_error <= target_radius,
                            "yaw_within_tolerance": yaw_within_tolerance,
                            "controller_completed": controller_completed,
                            "robot_safe": not payload.safety_tripped,
                        },
                        "success": success,
                        "target_error_m": target_error,
                        "yaw_error_rad": yaw_error,
                        "maximum_registered_corner_error_m": shape_error.maximum_corner_error_m,
                        "rms_registered_corner_error_m": shape_error.rms_corner_error_m,
                        "terminal": True,
                    },
                    provenance=(deployment_ref,),
                    validation_conditions=("evaluation occurs only after terminal Reality deployment",),
                )
            )
            send_result(connection, reference)
        except BaseException as error:
            send_error(connection, error)
