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
from .friction_artifacts import TrajectoryDatasetPayload, default_world_spec_seed
from .newton_deployment import close_capability_sessions, execute_deployment
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
    task_id: str,
) -> None:
    """Own hidden truth for both experiment and a newly allocated deployment world."""

    from dataclasses import replace

    import numpy as np

    from curious_robot.belief import initial_belief
    from curious_robot.environment import TruthEnvironment
    from curious_robot.objects import get_object_archetype
    from curious_robot.ports import RobotRealityAdapter
    from curious_robot.surface import sample_surface_profile
    from curious_robot.tasks import get_task_preset
    from curious_robot.tools import RobotTools
    from curious_robot.world_config import (
        PhysicalParameters,
        WorldConfig,
        object_kind_from_archetype_id,
    )

    store = open_agent_artifact_capability(
        artifact_root,
        read_kinds=frozenset(
            {
                LabArtifactKind.CAPABILITY_ARTIFACT.value,
                LabArtifactKind.CAPABILITY_ATTESTATION.value,
                LabArtifactKind.CAPABILITY_GAP.value,
                LabArtifactKind.POLICY_ARTIFACT.value,
                LabArtifactKind.REALITY_DEPLOYMENT.value,
                LabArtifactKind.TRAJECTORY_DATASET.value,
                LabArtifactKind.TWIN_VALIDATION_REPORT.value,
            }
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
    task_preset = get_task_preset(task_id)
    object_kind = object_kind_from_archetype_id(task_preset.object_archetype_id)
    object_archetype = get_object_archetype(
        task_preset.object_archetype_id,
        task_preset.object_archetype_version,
    )
    hidden_parameters = PhysicalParameters(
        mass_kg=float(scenario_rng.uniform(*task_preset.default_mass_range_kg)),
        static_friction=surface.effective_static_friction,
        dynamic_friction=surface.effective_dynamic_friction,
        com_m=object_archetype.uniform_density_centroid_m,
        restitution=surface.restitution,
    )
    object_support_z_m = max(
        abs(part.center_object_m[2]) + part.half_extents_m[2]
        for part in object_archetype.parts
    )
    x_low, x_high, y_low, y_high = task_preset.initial_position_jitter_m
    hidden_initial_position = (
        float(task_preset.initial_pose.x_m + scenario_rng.uniform(x_low, x_high)),
        float(task_preset.initial_pose.y_m + scenario_rng.uniform(y_low, y_high)),
        object_support_z_m,
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
    capability_sessions: dict[str, dict] = {}

    while True:
        message = connection.recv()
        command = message.get("command")
        if command == "shutdown":
            close_capability_sessions(capability_sessions)
            return
        try:
            if command != "execute" or not isinstance(message.get("payload"), OperationRequest):
                raise ValueError("unsupported Reality RPC command")
            request = message["payload"]
            arguments = request.arguments
            if not isinstance(arguments, dict):
                raise TypeError("Reality operation arguments must be an object")
            if request.operation == LabOperation.FRICTION_EXPERIMENT.value:
                if arguments.get("task_id", "push-cube-v1") != task_id:
                    raise ValueError("Reality worker task does not match the requested experiment")
                capability_ref = _reference(arguments["provenance"][0], LabArtifactKind.CAPABILITY_GAP.value)
                store.get(capability_ref)
                config = WorldConfig(
                    task_id=task_id,
                    device=device,
                    initial_position_m=hidden_initial_position,
                    initial_yaw_rad=task_preset.initial_pose.yaw_rad,
                    target_position_m=(
                        task_preset.goal.target.x_m,
                        task_preset.goal.target.y_m,
                        object_support_z_m,
                    ),
                    target_radius_m=task_preset.goal.position_tolerance_m,
                    target_yaw_rad=task_preset.goal.target.yaw_rad,
                    target_yaw_tolerance_rad=task_preset.goal.yaw_tolerance_rad,
                    include_robot=True,
                    object_kind=object_kind,
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
                    if task_id == "push-t-bar-v1":
                        producer = "Newton SensorContact Franka end-effector ↔ t-bar"
                        static_evidence = static_evidence.model_copy(
                            update={"metadata": {**static_evidence.metadata, "producer": producer}}
                        )
                        dynamic_evidence = dynamic_evidence.model_copy(
                            update={"metadata": {**dynamic_evidence.metadata, "producer": producer}}
                        )
                    experiment_world_id = world.world_instance_id
                    spec_seed = default_world_spec_seed(
                        initial_position_m=observed_initial,
                        target_position_m=config.target_position_m,
                        target_radius_m=config.target_radius_m,
                        world_config=_public_world_config(public_config),
                        protocol=tools.protocol,
                        task_id=task_id,
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
                        "excessive_yaw_motion",
                        "insufficient_native_contact_samples",
                        "insufficient_contact_coverage",
                    }.intersection((*static_evidence.quality_flags, *dynamic_evidence.quality_flags))
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
                                    "samples come from native robot-tool to object contacts",
                                    "experiment contains the protocol minimum contact coverage",
                                ),
                            )
                        )
                        result = OperationResult(request.request_id, "completed", (reference,))
                finally:
                    world.close()
            elif request.operation in {
                LabOperation.DEPLOY_POLICY.value,
                LabOperation.DEPLOY_CAPABILITY.value,
            }:
                result = execute_deployment(
                    request=request,
                    arguments=arguments,
                    store=store,
                    identity=identity,
                    hidden_parameters=hidden_parameters,
                    hidden_initial_position=hidden_initial_position,
                    experiment_world_id=experiment_world_id,
                    capability_sessions=capability_sessions,
                    device=device,
                    seed=seed,
                )
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
        "initial_yaw_rad": config.initial_yaw_rad,
        "maximum_cartesian_speed_m_s": config.maximum_cartesian_speed_m_s,
        "maximum_tool_force_n": config.maximum_tool_force_n,
        "observation_noise_std": config.observation_noise_std,
        "prediction_residual_tolerance_m": config.prediction_residual_tolerance_m,
        "seed": config.seed,
        "target_position_m": list(config.target_position_m),
        "target_radius_m": config.target_radius_m,
    }
