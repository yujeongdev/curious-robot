"""Hardware Reality adapter shared by direct FCI and ROS 2 transports."""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from curious_robot.lab.artifact_store import ScopedArtifactStore
from curious_robot.lab.capability_artifact import validate_capability_invocation
from curious_robot.lab.capability_skill import DeterministicFrictionSkill
from curious_robot.lab.contracts import (
    ArtifactEnvelope,
    ArtifactRef,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
    OperationResult,
)
from curious_robot.lab.friction_artifacts import RealityDeploymentPayload
from curious_robot.lab.ports import PortAuthority, RuntimeIdentity
from curious_robot.objects import get_object_archetype
from curious_robot.tasks import get_task_preset

from .action_graph import RuntimeCapabilityManifest, admit_deployment_bundle
from .hardware_backends import (
    CANONICAL_BACKEND_CONTRACTS,
    HardwareBackendContract,
    HardwareBackendProbe,
    is_canonical_probe,
)
from .hardware_interfaces import (
    FrankaHardwareTransport,
    HardwareEpisode,
    HardwareMotionPreflight,
    HardwareObservation,
    HardwarePerception,
    HardwarePushResult,
)
from .hardware_motion import (
    FrankaPushCalibration,
    FrankaPushMotionPlan,
    HardwareToolPose,
    compile_franka_deployment_bundle,
    compile_franka_push_plan,
    transform_capability_position,
)

REQUIRED_PREFLIGHT_CHECKS = frozenset(
    {
        "ik",
        "joint_limits",
        "singularity",
        "self_collision",
        "environment_collision",
        "orientation",
    }
)


@dataclass(slots=True)
class _HardwareSession:
    episode: HardwareEpisode
    next_attempt: int = 1
    closed: bool = False
    episode_ended: bool = False
    peak_force_n: float = 0.0
    attempts: list[dict] | None = None

    def __post_init__(self) -> None:
        if self.attempts is None:
            self.attempts = []


class FrankaHardwareReality:
    """Execute only attested capabilities through an independently bounded transport."""

    operations = frozenset({LabOperation.DEPLOY_CAPABILITY.value})
    evidence_kinds = frozenset({LabArtifactKind.REALITY_DEPLOYMENT.value})

    def __init__(
        self,
        *,
        probe: HardwareBackendProbe,
        transport: FrankaHardwareTransport,
        perception: HardwarePerception,
        artifact_store: ScopedArtifactStore,
        push_calibration: FrankaPushCalibration,
        command_timeout_s: float = 60.0,
        perception_timeout_s: float = 2.0,
        observation_max_age_s: float = 0.25,
        observation_max_covariance_trace_m2: float = 0.0001,
        expected_perception_source_id: str = "external-perception",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        canonical = CANONICAL_BACKEND_CONTRACTS.get(probe.contract.kind)
        if canonical is None or not is_canonical_probe(probe):
            raise ValueError("hardware backend probe does not use a canonical contract")
        if not probe.ready:
            raise ValueError(f"hardware backend is not ready: {probe.blockers}")
        if probe.contract.kind.value != transport.backend_name:
            raise ValueError("selected backend and transport do not match")
        if probe.readiness.approved_calibration_id != push_calibration.calibration_id:
            raise ValueError("readiness evidence does not approve this calibration")
        if command_timeout_s <= 0 or not math.isfinite(command_timeout_s):
            raise ValueError("command timeout must be finite and positive")
        if perception_timeout_s <= 0 or not math.isfinite(perception_timeout_s):
            raise ValueError("perception timeout must be finite and positive")
        if observation_max_age_s <= 0 or not math.isfinite(observation_max_age_s):
            raise ValueError("observation age limit must be finite and positive")
        if observation_max_covariance_trace_m2 < 0 or not math.isfinite(observation_max_covariance_trace_m2):
            raise ValueError("observation covariance limit must be finite and non-negative")
        if not expected_perception_source_id:
            raise ValueError("expected perception source must not be empty")
        self.probe = probe
        self.transport = transport
        self.perception = perception
        self.store = artifact_store
        self.push_calibration = push_calibration
        self.runtime_manifest = RuntimeCapabilityManifest(
            backend_id=probe.contract.kind.value,
            executor_abi=probe.contract.executor_abi,
            supported_graph_schema_versions=("action-graph-v1",),
            capabilities=probe.contract.action_graph_capabilities,
            approved_calibration_ids=(push_calibration.calibration_id,),
            maximum_force_n=probe.readiness.approved_maximum_force_n or 0.0,
            maximum_speed_m_s=probe.readiness.approved_maximum_speed_m_s or 0.0,
            realtime_ready=probe.readiness.realtime_host_ready,
            watchdog_ready=(
                probe.readiness.independent_safety_ready
                and probe.readiness.command_timeout_configured
            ),
        )
        self.command_timeout_s = command_timeout_s
        self.perception_timeout_s = perception_timeout_s
        self.observation_max_age_s = observation_max_age_s
        self.observation_max_covariance_trace_m2 = observation_max_covariance_trace_m2
        self.expected_frame_id = push_calibration.hardware_frame_id
        self.expected_perception_source_id = expected_perception_source_id
        self.expected_object_id = push_calibration.object_id
        self._monotonic = monotonic
        self.identity = _hardware_identity(probe.contract)
        self._sessions: dict[str, _HardwareSession] = {}
        self._pending_episode_cleanup: list[_HardwareSession] = []
        self._used_episode_ids: set[str] = set()
        self._closed = False
        self._closing = False
        self._command_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

    def execute(self, request: OperationRequest) -> OperationResult:
        if not self._is_open():
            return self._failure(request, "hardware_reality_closed")
        if request.operation != LabOperation.DEPLOY_CAPABILITY.value:
            return OperationResult(request.request_id, "rejected", detail="unsupported operation")
        if not self._command_lock.acquire(blocking=False):
            return self._failure(request, "hardware_command_busy")
        try:
            if not self._is_open():
                return self._failure(request, "hardware_reality_closed")
            return self._execute_serialized(request)
        finally:
            self._command_lock.release()

    def _execute_serialized(self, request: OperationRequest) -> OperationResult:
        graph_started_s = self._monotonic()
        arguments = request.arguments
        if not isinstance(arguments, dict):
            return self._failure(request, "arguments_not_object")
        provenance: tuple[ArtifactRef, ...] = ()
        active_session: _HardwareSession | None = None
        watchdog_token: str | None = None
        try:
            capability_ref = _reference(
                arguments.get("capability_artifact_id", ""),
                LabArtifactKind.CAPABILITY_ARTIFACT.value,
            )
            provenance = (capability_ref,)
            skill = DeterministicFrictionSkill.load(self.store, capability_ref)
            invocation = validate_capability_invocation(skill.payload, capability_ref, arguments)
            policy_ref = _reference(
                skill.payload.policy_artifact_id,
                LabArtifactKind.POLICY_ARTIFACT.value,
            )
            policy = self.store.get(policy_ref)
            if not isinstance(policy.payload, dict):
                raise TypeError("hardware policy payload must be an object")
            task_id = str(policy.payload.get("task_id", "push-cube-v1"))
            task = get_task_preset(task_id)
            archetype = get_object_archetype(
                task.object_archetype_id,
                task.object_archetype_version,
            )
            provenance = (policy_ref, capability_ref)
            session_key = f"{capability_ref.artifact_id}:{invocation.invocation_id}"
            session = self._sessions.get(session_key)
            if session is None:
                episode = self.perception.start_episode(
                    invocation.invocation_id,
                    self.perception_timeout_s,
                )
                incoming_session = _HardwareSession(episode)
                active_session = incoming_session
                if episode.invocation_id != invocation.invocation_id:
                    raise ValueError("hardware episode is bound to another invocation")
                if episode.episode_id in self._used_episode_ids:
                    owner = next(
                        (
                            candidate
                            for candidate in self._sessions.values()
                            if candidate.episode.episode_id == episode.episode_id
                            and not candidate.episode_ended
                        ),
                        None,
                    )
                    if owner is not None:
                        active_session = owner
                    raise ValueError("hardware perception reused an episode id")
                self._used_episode_ids.add(episode.episode_id)
                session = incoming_session
                self._validate_observation(
                    episode.initial_observation,
                    expected_episode_id=episode.episode_id,
                )
                self._sessions[session_key] = session
            active_session = session
            if session.closed or invocation.attempt_index != session.next_attempt:
                raise ValueError("hardware capability attempt was replayed or reset")
            expected_initial_position = transform_capability_position(
                skill.payload.applicability.validated_initial_position_m,
                self.push_calibration,
            )
            if (
                math.dist(
                    session.episode.initial_observation.position_m,
                    expected_initial_position,
                )
                > skill.payload.applicability.initial_position_tolerance_m
            ):
                raise ValueError("hardware initial pose is outside capability applicability")
            precommand_observation = (
                session.episode.initial_observation
                if invocation.attempt_index == 1
                else self.perception.observe_object_position(self.perception_timeout_s)
            )
            self._validate_observation(
                precommand_observation,
                expected_episode_id=session.episode.episode_id,
            )
            tool_pose = self.transport.read_tool_pose(self.perception_timeout_s)
            self._validate_tool_pose(tool_pose)
            motion_plan = compile_franka_push_plan(
                task_id=task.preset_id,
                task_version=task.version,
                action=skill.payload.action_template,
                protocol=skill.payload.protocol.final_push,
                capability_frame_id=skill.payload.applicability.frame_id,
                capability_object_id=skill.payload.applicability.object_id,
                object_archetype_version=archetype.version,
                geometry_hash=archetype.geometry_hash,
                current_object_position_m=precommand_observation.position_m,
                current_object_orientation_xyzw=precommand_observation.orientation_xyzw,
                current_tool_pose=tool_pose,
                calibration=self.push_calibration,
                maximum_force_n=skill.payload.safety.maximum_force_n,
                maximum_speed_m_s=skill.payload.safety.maximum_speed_m_s,
                command_timeout_s=self.command_timeout_s,
                observation_timeout_s=self.perception_timeout_s,
            )
            deployment_bundle = compile_franka_deployment_bundle(
                motion_plan,
                validation_artifact_ids=tuple(
                    sorted((policy_ref.artifact_id, capability_ref.artifact_id))
                ),
                executor_abi=self.probe.contract.executor_abi,
            )
            admission = admit_deployment_bundle(deployment_bundle, self.runtime_manifest)
            if not admission.admitted:
                raise RuntimeError(
                    "hardware runtime rejected deployment bundle: "
                    + ", ".join(admission.blockers)
                )
            expected_plan_id = motion_plan.plan_id
            expected_graph_id = motion_plan.action_graph.graph_id
            canonical_plan = motion_plan.model_dump_json(exclude_none=False)
            preflight = self.transport.validate_motion_plan(
                motion_plan,
                self.perception_timeout_s,
            )
            _validate_preflight(
                preflight,
                expected_plan_id,
                expected_graph_id,
            )
            motion_plan = FrankaPushMotionPlan.model_validate_json(canonical_plan)
            if (
                motion_plan.plan_id != expected_plan_id
                or motion_plan.action_graph.graph_id != expected_graph_id
                or deployment_bundle.motion_plan_id != expected_plan_id
            ):
                raise ValueError("hardware preflight mutated the admitted deployment plan")
            remaining_graph_time_s = _remaining_graph_time_s(
                started_s=graph_started_s,
                deadline_s=motion_plan.action_graph.wall_clock_deadline_s,
                monotonic=self._monotonic,
            )
            if motion_plan.planned_duration_s > remaining_graph_time_s:
                raise TimeoutError("insufficient action-graph time remains for the motion plan")
            transport_timeout_s = min(self.command_timeout_s, remaining_graph_time_s)
            watchdog_token = self.transport.arm_watchdog(transport_timeout_s)
            if not watchdog_token:
                raise ValueError("hardware transport did not arm an independent watchdog")
            result = self.transport.execute_bounded_push(
                motion_plan=motion_plan,
                deployment_bundle_id=deployment_bundle.bundle_id,
                maximum_force_n=motion_plan.maximum_force_n,
                maximum_speed_m_s=skill.payload.safety.maximum_speed_m_s,
                command_timeout_s=transport_timeout_s,
            )
            self._require_open()
            _validate_push_result(
                result,
                maximum_force_n=motion_plan.maximum_force_n,
                maximum_speed_m_s=skill.payload.safety.maximum_speed_m_s,
                command_timeout_s=transport_timeout_s,
                watchdog_token=watchdog_token,
                motion_plan_id=motion_plan.plan_id,
                action_graph_id=motion_plan.action_graph.graph_id,
                deployment_bundle_id=deployment_bundle.bundle_id,
                calibration_id=motion_plan.calibration_id,
                maximum_acceleration_m_s2=motion_plan.maximum_linear_acceleration_m_s2,
                maximum_angular_speed_rad_s=motion_plan.maximum_angular_speed_rad_s,
                maximum_angular_acceleration_rad_s2=(motion_plan.maximum_angular_acceleration_rad_s2),
                orientation_tolerance_rad=motion_plan.orientation_tolerance_rad,
            )
            self.transport.disarm_watchdog(watchdog_token)
            watchdog_token = None
            final_observation = self.perception.observe_object_position(self.perception_timeout_s)
            self._validate_observation(
                final_observation,
                expected_episode_id=session.episode.episode_id,
            )
            _remaining_graph_time_s(
                started_s=graph_started_s,
                deadline_s=motion_plan.action_graph.wall_clock_deadline_s,
                monotonic=self._monotonic,
            )
            final_position = final_observation.position_m
            hardware_target = transform_capability_position(
                skill.payload.applicability.validated_target_m,
                self.push_calibration,
            )
            target_error = math.dist(
                final_position[:2],
                hardware_target[:2],
            )
            _remaining_graph_time_s(
                started_s=graph_started_s,
                deadline_s=motion_plan.action_graph.wall_clock_deadline_s,
                monotonic=self._monotonic,
            )
            session.attempts.append(
                {
                    "controller_outcome": result.controller_outcome,
                    "target_error_m": target_error,
                }
            )
            session.peak_force_n = max(session.peak_force_n, result.peak_contact_force_n)
            session.next_attempt += 1
            session.closed = (
                target_error <= skill.payload.applicability.validated_target_radius_m
                or result.safety_tripped
                or result.controller_outcome != "completed"
                or len(session.attempts) >= skill.payload.recovery.maximum_attempts
            )
            if session.closed:
                self.perception.end_episode(session.episode.episode_id)
                session.episode_ended = True
            payload = RealityDeploymentPayload(
                action_id=skill.payload.action_template.action_id,
                attempt_count=len(session.attempts),
                attempts=session.attempts,
                controller_outcome=result.controller_outcome,
                request_id=request.request_id,
                invocation_id=invocation.invocation_id,
                attempt_index=invocation.attempt_index,
                fresh_episode=True,
                frame_id=self.push_calibration.hardware_frame_id,
                object_id=self.push_calibration.object_id,
                motion_plan_id=motion_plan.plan_id,
                action_graph_id=motion_plan.action_graph.graph_id,
                deployment_bundle_id=deployment_bundle.bundle_id,
                calibration_id=motion_plan.calibration_id,
                maximum_measured_speed_m_s=result.maximum_measured_speed_m_s,
                maximum_measured_acceleration_m_s2=(result.maximum_measured_acceleration_m_s2),
                maximum_measured_angular_speed_rad_s=(result.maximum_measured_angular_speed_rad_s),
                maximum_measured_angular_acceleration_rad_s2=(
                    result.maximum_measured_angular_acceleration_rad_s2
                ),
                maximum_measured_orientation_error_rad=(result.maximum_measured_orientation_error_rad),
                motion_elapsed_s=result.elapsed_s,
                observed_final_position_m=final_position,
                peak_contact_force_n=session.peak_force_n,
                prediction_target_error_m=target_error,
                runtime_process_id=self.identity.process_id,
                safety_tripped=result.safety_tripped,
                target_position_m=hardware_target,
                target_radius_m=skill.payload.applicability.validated_target_radius_m,
                terminal=True,
                world_instance_id=session.episode.episode_id,
            )
            with self._lifecycle_lock:
                if self._closed or self._closing:
                    raise RuntimeError("hardware reality is closing")
                evidence = self.store.put(
                    ArtifactEnvelope(
                        kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
                        payload=payload.model_dump(mode="json"),
                        provenance=provenance,
                        validation_conditions=(
                            "hardware command passed capability and independent safety gates",
                            "external perception supplied the terminal object pose",
                        ),
                    )
                )
            return OperationResult(request.request_id, "completed", (evidence,))
        except Exception as error:
            cleanup_errors = self._abort(active_session, watchdog_token)
            error_type = type(error).__name__
            if cleanup_errors:
                error_type += "+cleanup:" + ",".join(cleanup_errors)
            return self._failure(request, error_type, provenance=provenance)

    def _validate_observation(
        self,
        observation: HardwareObservation,
        *,
        expected_episode_id: str,
    ) -> None:
        age_s = self._monotonic() - observation.captured_monotonic_s
        if age_s < 0 or age_s > self.observation_max_age_s:
            raise ValueError("hardware perception observation is stale or future-dated")
        if observation.frame_id != self.expected_frame_id:
            raise ValueError("hardware perception frame does not match")
        if observation.source_id != self.expected_perception_source_id:
            raise ValueError("hardware perception source does not match")
        if observation.object_id != self.expected_object_id:
            raise ValueError("hardware perception object does not match")
        if observation.episode_id != expected_episode_id:
            raise ValueError("hardware perception episode does not match")
        if observation.covariance_trace_m2 > self.observation_max_covariance_trace_m2:
            raise ValueError("hardware perception uncertainty exceeds the bound")

    def _validate_tool_pose(self, pose: HardwareToolPose) -> None:
        age_s = self._monotonic() - pose.captured_monotonic_s
        if age_s < 0 or age_s > self.observation_max_age_s:
            raise ValueError("hardware tool pose is stale or future-dated")
        if pose.frame_id != self.push_calibration.hardware_frame_id:
            raise ValueError("hardware tool pose frame does not match calibration")
        if pose.tool_id != self.push_calibration.tool_id:
            raise ValueError("hardware tool identity does not match calibration")
        if pose.calibration_id != self.push_calibration.calibration_id:
            raise ValueError("hardware tool pose uses another calibration")

    def _is_open(self) -> bool:
        with self._lifecycle_lock:
            return not self._closed and not self._closing

    def _require_open(self) -> None:
        if not self._is_open():
            raise RuntimeError("hardware reality is closing")

    def _abort(
        self,
        session: _HardwareSession | None,
        watchdog_token: str | None,
    ) -> list[str]:
        errors: list[str] = []
        try:
            self.transport.safe_stop()
        except Exception as error:
            errors.append(f"safe_stop_{type(error).__name__}")
        if watchdog_token is not None:
            try:
                self.transport.disarm_watchdog(watchdog_token)
            except Exception as error:
                errors.append(f"watchdog_{type(error).__name__}")
        if session is not None and not session.episode_ended:
            session.closed = True
            try:
                self.perception.end_episode(session.episode.episode_id)
                session.episode_ended = True
                if session in self._pending_episode_cleanup:
                    self._pending_episode_cleanup.remove(session)
            except Exception as error:
                errors.append(f"perception_{type(error).__name__}")
                if session not in self._pending_episode_cleanup:
                    self._pending_episode_cleanup.append(session)
        return errors

    def close(self) -> None:
        """Stop motion and release every active hardware/perception resource."""

        with self._lifecycle_lock:
            if self._closed or self._closing:
                return
            self._closing = True
        cleanup_errors = self._abort(None, None)
        with self._command_lock:
            if self._closed:
                with self._lifecycle_lock:
                    self._closing = False
                return
            sessions = list(self._sessions.values()) + list(self._pending_episode_cleanup)
            for session in sessions:
                if not session.episode_ended:
                    session.closed = True
                    try:
                        self.perception.end_episode(session.episode.episode_id)
                        session.episode_ended = True
                        if session in self._pending_episode_cleanup:
                            self._pending_episode_cleanup.remove(session)
                    except Exception as error:
                        cleanup_errors.append(f"perception_{type(error).__name__}")
            try:
                self.transport.close()
            finally:
                with self._lifecycle_lock:
                    self._closed = True
                    self._closing = False
        if cleanup_errors:
            raise RuntimeError("hardware cleanup failed: " + ", ".join(cleanup_errors))

    def __enter__(self) -> FrankaHardwareReality:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def capture_evidence(self) -> tuple[ArtifactRef, ...]:
        return ()

    def _failure(
        self,
        request: OperationRequest,
        error_type: str,
        *,
        provenance: tuple[ArtifactRef, ...] = (),
    ) -> OperationResult:
        failure = self.store.record_validation_failure(
            attempted_kind=LabArtifactKind.REALITY_DEPLOYMENT.value,
            rejected_payload={"request_id": request.request_id},
            errors=(error_type,),
            provenance=provenance,
            validation_conditions=("hardware capability execution completes safely",),
        )
        return OperationResult(request.request_id, "failed", (failure,))


def _hardware_identity(contract: HardwareBackendContract) -> RuntimeIdentity:
    nonce = str(uuid4())
    return RuntimeIdentity(
        authority=PortAuthority.REALITY,
        process_id=os.getpid(),
        world_id=f"franka-hardware:{nonce}",
        model_id=f"franka-model:{contract.kind.value}:{nonce}",
        state_id=f"franka-state:{nonce}",
        config_id=f"hardware-backend:{contract.kind.value}:{nonce}",
    )


def _validate_push_result(
    result: HardwarePushResult,
    *,
    maximum_force_n: float,
    maximum_speed_m_s: float,
    command_timeout_s: float,
    watchdog_token: str,
    motion_plan_id: str,
    action_graph_id: str,
    deployment_bundle_id: str,
    calibration_id: str,
    maximum_acceleration_m_s2: float,
    maximum_angular_speed_rad_s: float,
    maximum_angular_acceleration_rad_s2: float,
    orientation_tolerance_rad: float,
) -> None:
    if (
        result.motion_plan_id != motion_plan_id
        or result.action_graph_id != action_graph_id
        or result.deployment_bundle_id != deployment_bundle_id
        or result.calibration_id != calibration_id
    ):
        raise ValueError("hardware transport result attests another graph, plan, or calibration")
    if not result.deadline_enforced or result.watchdog_token != watchdog_token:
        raise ValueError("hardware transport did not attest the armed deadline")
    if result.elapsed_s > command_timeout_s:
        raise ValueError("hardware transport exceeded the command deadline")
    if result.peak_contact_force_n > maximum_force_n:
        raise ValueError("hardware transport exceeded the capability force limit")
    if result.maximum_measured_speed_m_s > maximum_speed_m_s:
        raise ValueError("hardware transport exceeded the capability speed limit")
    if result.maximum_measured_acceleration_m_s2 > maximum_acceleration_m_s2:
        raise ValueError("hardware transport exceeded the calibrated acceleration limit")
    if result.maximum_measured_angular_speed_rad_s > maximum_angular_speed_rad_s:
        raise ValueError("hardware transport exceeded the calibrated angular speed limit")
    if result.maximum_measured_angular_acceleration_rad_s2 > maximum_angular_acceleration_rad_s2:
        raise ValueError("hardware transport exceeded the calibrated angular acceleration limit")
    if result.maximum_measured_orientation_error_rad > orientation_tolerance_rad:
        raise ValueError("hardware transport exceeded the calibrated orientation tolerance")
    expected_trip = result.controller_outcome == "safety_stopped"
    if result.safety_tripped != expected_trip:
        raise ValueError("hardware transport returned inconsistent safety status")


def _validate_preflight(
    preflight: HardwareMotionPreflight,
    plan_id: str,
    action_graph_id: str,
) -> None:
    if preflight.plan_id != plan_id or preflight.action_graph_id != action_graph_id:
        raise ValueError("hardware preflight attests another action graph or motion plan")
    if frozenset(preflight.checks) != REQUIRED_PREFLIGHT_CHECKS or len(preflight.checks) != len(
        REQUIRED_PREFLIGHT_CHECKS
    ):
        raise ValueError("hardware preflight did not perform every required check")
    if not preflight.feasible or preflight.errors:
        raise ValueError("hardware motion plan failed transport preflight")


def _remaining_graph_time_s(
    *,
    started_s: float,
    deadline_s: float,
    monotonic: Callable[[], float],
) -> float:
    remaining_s = deadline_s - (monotonic() - started_s)
    if remaining_s <= 0.0:
        raise TimeoutError("hardware action graph exceeded its wall-clock deadline")
    return remaining_s


def _reference(artifact_id: str, kind: str) -> ArtifactRef:
    from curious_robot.lab.contracts import ArtifactOutcome, ArtifactVisibility

    return ArtifactRef(artifact_id, kind, ArtifactVisibility.AGENT, ArtifactOutcome.PASS)
