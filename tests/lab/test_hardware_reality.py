from __future__ import annotations

import threading

import pytest
from test_capability_skill import _successful_result

from curious_robot.hardware_backends import (
    PYLIBFRANKA_CONTRACT,
    HardwareBackendContract,
    HardwareBackendKind,
    HardwareBackendProbe,
    HardwareBackendUnavailable,
    HardwareReadinessEvidence,
    probe_hardware_backends,
    select_hardware_backend,
)
from curious_robot.hardware_factory import build_franka_hardware_reality
from curious_robot.hardware_interfaces import (
    HardwareEpisode,
    HardwareObservation,
    HardwarePushResult,
)
from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.capability_artifact import distill_friction_capability
from curious_robot.lab.capability_skill import DeterministicFrictionSkill, SkillStatus
from curious_robot.lab.contracts import LabOperation, OperationRequest
from curious_robot.lab.friction_artifacts import RealityDeploymentPayload

NOW = 100.0


def _ready(**updates: bool) -> HardwareReadinessEvidence:
    values = {
        "robot_reachable": True,
        "external_perception_ready": True,
        "independent_safety_ready": True,
        "command_timeout_configured": True,
        "exclusive_control_confirmed": True,
        "realtime_host_ready": True,
        "state_streaming": True,
        "hardware_component_active": True,
        "command_controller_active": True,
    }
    values.update(updates)
    return HardwareReadinessEvidence(**values)


class FakePerception:
    def __init__(self, initial, final, *, captured_at: float = NOW) -> None:  # noqa: ANN001
        self.initial = initial
        self.final = final
        self.captured_at = captured_at
        self.ended = []
        self.episode_id = None
        self.current_episode_id = "not-started"
        self.final_episode_id = None
        self.invocation_override = None
        self.end_attempts = 0
        self.end_failures_remaining = 0

    def _observation(  # noqa: ANN001
        self, position, *, episode_id: str | None = None
    ) -> HardwareObservation:
        return HardwareObservation(
            episode_id=episode_id or self.current_episode_id,
            object_id="cube",
            position_m=position,
            frame_id="world",
            captured_monotonic_s=self.captured_at,
            covariance_trace_m2=0.00001,
            source_id="external-perception",
        )

    def start_episode(self, invocation_id: str, timeout_s: float) -> HardwareEpisode:
        del timeout_s
        self.current_episode_id = self.episode_id or f"hardware:{invocation_id}"
        return HardwareEpisode(
            episode_id=self.current_episode_id,
            invocation_id=self.invocation_override or invocation_id,
            initial_observation=self._observation(self.initial),
        )

    def observe_object_position(self, timeout_s: float) -> HardwareObservation:
        del timeout_s
        return self._observation(self.final, episode_id=self.final_episode_id)

    def end_episode(self, episode_id: str) -> None:
        self.end_attempts += 1
        if self.end_failures_remaining:
            self.end_failures_remaining -= 1
            raise RuntimeError("perception cleanup failed")
        self.ended.append(episode_id)


class FakeTransport:
    def __init__(self, backend: HardwareBackendKind) -> None:
        self.backend_name = backend.value
        self.calls = []
        self.safe_stop_calls = 0
        self.safe_stop_event = threading.Event()
        self.close_calls = 0
        self.disarmed = []
        self.result_updates = {}
        self.fail_safe_stop = False
        self.fail_close = False

    def arm_watchdog(self, command_timeout_s: float) -> str:
        assert command_timeout_s > 0
        return "watchdog-1"

    def disarm_watchdog(self, watchdog_token: str) -> None:
        self.disarmed.append(watchdog_token)

    def safe_stop(self) -> None:
        self.safe_stop_calls += 1
        self.safe_stop_event.set()
        if self.fail_safe_stop:
            raise RuntimeError("safe stop failed")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("close failed")

    def execute_bounded_push(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        values = {
            "controller_outcome": "completed",
            "peak_contact_force_n": 4.0,
            "maximum_measured_speed_m_s": 0.02,
            "elapsed_s": 1.0,
            "safety_tripped": False,
            "watchdog_token": "watchdog-1",
            "deadline_enforced": True,
        }
        values.update(self.result_updates)
        return HardwarePushResult(**values)


def _hardware_case(tmp_path, *, backend=HardwareBackendKind.PYLIBFRANKA):  # noqa: ANN001, ANN201
    stores = LaboratoryArtifactStores(tmp_path)
    capability_ref = distill_friction_capability(stores, _successful_result(stores))
    skill = DeterministicFrictionSkill.load(stores.agent, capability_ref)
    applicability = skill.payload.applicability
    transport = FakeTransport(backend)
    perception = FakePerception(
        applicability.validated_initial_position_m,
        (0.13, 0.0, 0.025),
    )
    probes = probe_hardware_backends(
        {backend: _ready()},
        module_available=lambda name: (
            name != "pylibfranka" or backend is HardwareBackendKind.PYLIBFRANKA
        ),
    )
    reality = build_franka_hardware_reality(
        artifact_root=tmp_path,
        probes=probes,
        transport_factories={backend: lambda: transport},
        perception=perception,
        monotonic=lambda: NOW,
    )
    decision = skill.begin(
        current_position_m=applicability.validated_initial_position_m,
        target_position_m=applicability.validated_target_m,
        target_radius_m=applicability.validated_target_radius_m,
        available_operations=reality.operations,
    )
    assert decision.request is not None
    return stores, skill, transport, perception, reality, decision.request


def test_backend_selection_prefers_fci_and_falls_back_to_ros2() -> None:
    readiness = {
        HardwareBackendKind.PYLIBFRANKA: _ready(),
        HardwareBackendKind.ROS2_CONTROL: _ready(),
    }
    both = probe_hardware_backends(readiness, module_available=lambda _name: True)
    fallback = probe_hardware_backends(
        readiness,
        module_available=lambda name: name != "pylibfranka",
    )

    assert select_hardware_backend(both).kind is HardwareBackendKind.PYLIBFRANKA
    assert select_hardware_backend(fallback).kind is HardwareBackendKind.ROS2_CONTROL


def test_backend_selection_rejects_partial_operational_readiness() -> None:
    readiness = {
        HardwareBackendKind.PYLIBFRANKA: _ready(realtime_host_ready=False),
        HardwareBackendKind.ROS2_CONTROL: _ready(command_controller_active=False),
    }
    probes = probe_hardware_backends(readiness, module_available=lambda _name: True)

    with pytest.raises(HardwareBackendUnavailable) as captured:
        select_hardware_backend(probes)

    assert "realtime_host_ready" in str(captured.value)
    assert "command_controller_active" in str(captured.value)


def test_hardware_reality_executes_only_guarded_capability(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    capability_ref = distill_friction_capability(stores, _successful_result(stores))
    skill = DeterministicFrictionSkill.load(stores.agent, capability_ref)
    applicability = skill.payload.applicability
    transport = FakeTransport(HardwareBackendKind.PYLIBFRANKA)
    probes = probe_hardware_backends(
        {HardwareBackendKind.PYLIBFRANKA: _ready()},
        module_available=lambda name: name == "pylibfranka",
    )
    reality = build_franka_hardware_reality(
        artifact_root=tmp_path,
        probes=probes,
        transport_factories={HardwareBackendKind.PYLIBFRANKA: lambda: transport},
        perception=FakePerception(
            applicability.validated_initial_position_m,
            (0.13, 0.0, 0.025),
        ),
        monotonic=lambda: NOW,
    )
    decision = skill.begin(
        current_position_m=applicability.validated_initial_position_m,
        target_position_m=applicability.validated_target_m,
        target_radius_m=applicability.validated_target_radius_m,
        available_operations=reality.operations,
    )
    assert decision.status is SkillStatus.EXECUTE
    assert decision.request is not None

    result = reality.execute(decision.request)
    deployment = RealityDeploymentPayload.model_validate(
        stores.agent.get(result.evidence[0]).payload
    )

    assert result.status == "completed"
    assert deployment.request_id == decision.request.request_id
    assert deployment.prediction_target_error_m == pytest.approx(0.02)
    assert transport.calls[0]["maximum_force_n"] == skill.payload.safety.maximum_force_n
    assert transport.calls[0]["command_timeout_s"] == 30.0

    bypass = reality.execute(
        OperationRequest(
            LabOperation.DEPLOY_POLICY.value,
            {"policy_artifact_id": skill.payload.policy_artifact_id},
            "bypass",
        )
    )
    assert bypass.status == "rejected"


def test_hardware_reality_rejects_widened_or_replayed_invocation(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    capability_ref = distill_friction_capability(stores, _successful_result(stores))
    skill = DeterministicFrictionSkill.load(stores.agent, capability_ref)
    applicability = skill.payload.applicability
    transport = FakeTransport(HardwareBackendKind.ROS2_CONTROL)
    probes = probe_hardware_backends(
        {HardwareBackendKind.ROS2_CONTROL: _ready()},
        module_available=lambda name: name != "pylibfranka",
    )
    reality = build_franka_hardware_reality(
        artifact_root=tmp_path,
        probes=probes,
        transport_factories={HardwareBackendKind.ROS2_CONTROL: lambda: transport},
        perception=FakePerception(
            applicability.validated_initial_position_m,
            (0.13, 0.0, 0.025),
        ),
        monotonic=lambda: NOW,
    )
    decision = skill.begin(
        current_position_m=applicability.validated_initial_position_m,
        target_position_m=applicability.validated_target_m,
        target_radius_m=applicability.validated_target_radius_m,
        available_operations=reality.operations,
    )
    assert decision.request is not None
    widened = OperationRequest(
        decision.request.operation,
        {**decision.request.arguments, "target_radius_m": 1.0},
        "widened",
    )

    assert reality.execute(widened).status == "failed"
    assert reality.execute(decision.request).status == "completed"
    assert reality.execute(decision.request).status == "failed"


def test_backend_selection_rejects_forged_contract() -> None:
    forged = HardwareBackendContract(
        kind=HardwareBackendKind.PYLIBFRANKA,
        required_modules=(),
        requires_realtime_host=False,
        state_surface=PYLIBFRANKA_CONTRACT.state_surface,
        command_surface=PYLIBFRANKA_CONTRACT.command_surface,
    )
    probe = HardwareBackendProbe(forged, (), _ready(realtime_host_ready=False))

    with pytest.raises(HardwareBackendUnavailable, match="non-canonical"):
        select_hardware_backend((probe,))


def test_builder_rejects_handcrafted_probe_even_with_canonical_contract(tmp_path) -> None:
    probe = HardwareBackendProbe(PYLIBFRANKA_CONTRACT, (), _ready())
    transport = FakeTransport(HardwareBackendKind.PYLIBFRANKA)

    with pytest.raises(HardwareBackendUnavailable, match="non-canonical"):
        build_franka_hardware_reality(
            artifact_root=tmp_path,
            probes=(probe,),
            transport_factories={HardwareBackendKind.PYLIBFRANKA: lambda: transport},
            perception=FakePerception((0.0, 0.0, 0.025), (0.0, 0.0, 0.025)),
            monotonic=lambda: NOW,
        )


def test_builder_falls_back_when_preferred_factory_fails(tmp_path) -> None:
    probes = probe_hardware_backends(
        {
            HardwareBackendKind.PYLIBFRANKA: _ready(),
            HardwareBackendKind.ROS2_CONTROL: _ready(),
        },
        module_available=lambda _name: True,
    )
    ros_transport = FakeTransport(HardwareBackendKind.ROS2_CONTROL)

    def fail_fci() -> FakeTransport:
        raise RuntimeError("FCI unavailable")

    reality = build_franka_hardware_reality(
        artifact_root=tmp_path,
        probes=probes,
        transport_factories={
            HardwareBackendKind.PYLIBFRANKA: fail_fci,
            HardwareBackendKind.ROS2_CONTROL: lambda: ros_transport,
        },
        perception=FakePerception((0.0, 0.0, 0.025), (0.0, 0.0, 0.025)),
        monotonic=lambda: NOW,
    )

    assert reality.transport is ros_transport


def test_builder_cleans_mismatched_transport_and_falls_back(tmp_path) -> None:
    probes = probe_hardware_backends(
        {
            HardwareBackendKind.PYLIBFRANKA: _ready(),
            HardwareBackendKind.ROS2_CONTROL: _ready(),
        },
        module_available=lambda _name: True,
    )
    mismatched = FakeTransport(HardwareBackendKind.ROS2_CONTROL)
    ros_transport = FakeTransport(HardwareBackendKind.ROS2_CONTROL)

    reality = build_franka_hardware_reality(
        artifact_root=tmp_path,
        probes=probes,
        transport_factories={
            HardwareBackendKind.PYLIBFRANKA: lambda: mismatched,
            HardwareBackendKind.ROS2_CONTROL: lambda: ros_transport,
        },
        perception=FakePerception((0.0, 0.0, 0.025), (0.0, 0.0, 0.025)),
        monotonic=lambda: NOW,
    )

    assert reality.transport is ros_transport
    assert mismatched.safe_stop_calls == 1
    assert mismatched.close_calls == 1


def test_builder_does_not_fallback_if_rejected_transport_cleanup_fails(tmp_path) -> None:
    probes = probe_hardware_backends(
        {
            HardwareBackendKind.PYLIBFRANKA: _ready(),
            HardwareBackendKind.ROS2_CONTROL: _ready(),
        },
        module_available=lambda _name: True,
    )
    mismatched = FakeTransport(HardwareBackendKind.ROS2_CONTROL)
    mismatched.fail_close = True
    ros_factory_calls = 0

    def ros_factory() -> FakeTransport:
        nonlocal ros_factory_calls
        ros_factory_calls += 1
        return FakeTransport(HardwareBackendKind.ROS2_CONTROL)

    with pytest.raises(HardwareBackendUnavailable, match="fallback is unsafe"):
        build_franka_hardware_reality(
            artifact_root=tmp_path,
            probes=probes,
            transport_factories={
                HardwareBackendKind.PYLIBFRANKA: lambda: mismatched,
                HardwareBackendKind.ROS2_CONTROL: ros_factory,
            },
            perception=FakePerception((0.0, 0.0, 0.025), (0.0, 0.0, 0.025)),
            monotonic=lambda: NOW,
        )

    assert ros_factory_calls == 0


def test_builder_cleans_transport_when_adapter_construction_fails(tmp_path) -> None:
    probes = probe_hardware_backends(
        {HardwareBackendKind.PYLIBFRANKA: _ready()},
        module_available=lambda _name: True,
    )
    transport = FakeTransport(HardwareBackendKind.PYLIBFRANKA)

    with pytest.raises(ValueError, match="command timeout"):
        build_franka_hardware_reality(
            artifact_root=tmp_path,
            probes=probes,
            transport_factories={HardwareBackendKind.PYLIBFRANKA: lambda: transport},
            perception=FakePerception((0.0, 0.0, 0.025), (0.0, 0.0, 0.025)),
            command_timeout_s=float("nan"),
            monotonic=lambda: NOW,
        )

    assert transport.safe_stop_calls == 1
    assert transport.close_calls == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"peak_contact_force_n": 100.0},
        {"maximum_measured_speed_m_s": 100.0},
        {"elapsed_s": 100.0},
        {"deadline_enforced": False},
        {"safety_tripped": True},
    ],
)
def test_hardware_result_violation_stops_and_records_failure(tmp_path, updates) -> None:  # noqa: ANN001
    _, _, transport, perception, reality, request = _hardware_case(tmp_path)
    transport.result_updates = updates

    result = reality.execute(request)

    assert result.status == "failed"
    assert transport.safe_stop_calls == 1
    assert transport.disarmed == ["watchdog-1"]
    assert perception.ended == [f"hardware:{request.arguments['invocation_id']}"]


def test_stale_perception_is_rejected_before_motion(tmp_path) -> None:
    stores, skill, transport, _, reality, request = _hardware_case(tmp_path)
    applicability = skill.payload.applicability
    stale = FakePerception(
        applicability.validated_initial_position_m,
        (0.13, 0.0, 0.025),
        captured_at=NOW - 1.0,
    )
    reality.perception = stale

    result = reality.execute(request)

    assert result.status == "failed"
    assert transport.calls == []
    assert transport.safe_stop_calls == 1
    assert stores.agent.get(result.evidence[0]).outcome.value == "FAIL"


def test_duplicate_episode_id_is_not_fresh(tmp_path) -> None:
    _, skill, transport, perception, reality, request = _hardware_case(tmp_path)
    perception.episode_id = "physical-episode-1"
    perception.final = skill.payload.applicability.validated_initial_position_m
    assert reality.execute(request).status == "completed"
    second = skill.begin(
        current_position_m=skill.payload.applicability.validated_initial_position_m,
        target_position_m=skill.payload.applicability.validated_target_m,
        target_radius_m=skill.payload.applicability.validated_target_radius_m,
        available_operations=reality.operations,
    )
    assert second.request is not None

    assert reality.execute(second.request).status == "failed"
    assert transport.safe_stop_calls == 1
    assert perception.ended == ["physical-episode-1"]


def test_terminal_observation_must_match_active_episode(tmp_path) -> None:
    _, _, transport, perception, reality, request = _hardware_case(tmp_path)
    perception.final_episode_id = "another-episode"

    result = reality.execute(request)

    assert result.status == "failed"
    assert transport.safe_stop_calls == 1
    assert perception.ended == [f"hardware:{request.arguments['invocation_id']}"]


def test_end_episode_failure_is_retried_by_abort(tmp_path) -> None:
    _, _, transport, perception, reality, request = _hardware_case(tmp_path)
    perception.end_failures_remaining = 1

    result = reality.execute(request)

    assert result.status == "failed"
    assert perception.end_attempts == 2
    assert perception.ended == [f"hardware:{request.arguments['invocation_id']}"]
    assert transport.safe_stop_calls == 1


def test_unbound_episode_cleanup_failure_is_retried(tmp_path) -> None:
    _, _, transport, perception, reality, request = _hardware_case(tmp_path)
    perception.invocation_override = "another-invocation"
    perception.end_failures_remaining = 1

    result = reality.execute(request)
    reality.close()

    assert result.status == "failed"
    assert perception.end_attempts == 2
    assert perception.ended == [f"hardware:{request.arguments['invocation_id']}"]
    assert transport.safe_stop_calls == 2


def test_busy_adapter_rejects_overlapping_command(tmp_path) -> None:
    _, _, transport, _, reality, request = _hardware_case(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_execute = transport.execute_bounded_push

    def blocking_execute(**kwargs):  # noqa: ANN003, ANN202
        entered.set()
        assert release.wait(timeout=2.0)
        return original_execute(**kwargs)

    transport.execute_bounded_push = blocking_execute
    first_result = []
    worker = threading.Thread(target=lambda: first_result.append(reality.execute(request)))
    worker.start()
    assert entered.wait(timeout=2.0)

    busy = reality.execute(request)
    release.set()
    worker.join(timeout=2.0)

    assert busy.status == "failed"
    assert first_result[0].status == "completed"
    assert len(transport.calls) == 1


def test_close_stops_and_releases_transport(tmp_path) -> None:
    _, _, transport, _, reality, request = _hardware_case(tmp_path)
    assert reality.execute(request).status == "completed"

    reality.close()

    assert transport.safe_stop_calls == 1
    assert transport.close_calls == 1
    assert reality.execute(request).status == "failed"


def test_close_cancels_then_waits_for_active_command(tmp_path) -> None:
    _, _, transport, _, reality, request = _hardware_case(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_execute = transport.execute_bounded_push

    def blocking_execute(**kwargs):  # noqa: ANN003, ANN202
        entered.set()
        assert release.wait(timeout=2.0)
        return original_execute(**kwargs)

    transport.execute_bounded_push = blocking_execute
    command_results = []
    command = threading.Thread(
        target=lambda: command_results.append(reality.execute(request))
    )
    command.start()
    assert entered.wait(timeout=2.0)
    closer = threading.Thread(target=reality.close)
    closer.start()

    assert transport.safe_stop_event.wait(timeout=2.0)
    assert transport.safe_stop_calls == 1
    assert transport.close_calls == 0
    assert reality.execute(request).status == "failed"
    release.set()
    command.join(timeout=2.0)
    closer.join(timeout=2.0)

    assert not command.is_alive()
    assert not closer.is_alive()
    assert transport.close_calls == 1
    assert command_results[0].status == "failed"
