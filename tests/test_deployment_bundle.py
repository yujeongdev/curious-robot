import pytest
from pydantic import ValidationError

from curious_robot.action_graph import (
    DeploymentBundle,
    DeploymentSafetyEnvelope,
    RuntimeCapabilityManifest,
    admit_deployment_bundle,
    compile_motion_action_graph,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def _bundle() -> DeploymentBundle:
    graph = compile_motion_action_graph(
        task_id="push-cube-v1",
        task_version="1.0.0",
        object_id="cube",
        object_archetype_version="1.0.0",
        geometry_hash=HASH_C,
        calibration_id=HASH_A,
        protocol_binding_id=HASH_B,
        phases=("precontact", "push", "retreat"),
        phase_durations_s=(0.5, 1.0, 0.5),
        contact_expectations=("unexpected", "maintain", "release"),
        observation_timeout_s=0.25,
        wall_clock_deadline_s=4.0,
    )
    return DeploymentBundle(
        bundle_name="cube-straight-push",
        task_id="push-cube-v1",
        task_version="1.0.0",
        object_id="cube",
        object_archetype_version="1.0.0",
        geometry_hash=HASH_C,
        protocol_binding_id=HASH_B,
        calibration_id=HASH_A,
        executor_abi="franka-action-graph-v1",
        required_backend_capabilities=(
            "external_object_pose",
            "cartesian_pose",
            "force_guard",
            "contact_monitor",
            "observation_quality_guard",
            "task_goal_evaluator",
        ),
        validation_artifact_ids=(HASH_C,),
        safety=DeploymentSafetyEnvelope(
            maximum_force_n=10.0,
            maximum_speed_m_s=0.04,
            execution_deadline_s=5.0,
        ),
        action_graph=graph,
    )


def _runtime(**updates) -> RuntimeCapabilityManifest:  # noqa: ANN003
    values = {
        "backend_id": "pylibfranka-native",
        "executor_abi": "franka-action-graph-v1",
        "supported_graph_schema_versions": ("action-graph-v1",),
        "capabilities": (
            "external_object_pose",
            "cartesian_pose",
            "force_guard",
            "contact_monitor",
            "observation_quality_guard",
            "task_goal_evaluator",
        ),
        "approved_calibration_ids": (HASH_A,),
        "maximum_force_n": 15.0,
        "maximum_speed_m_s": 0.1,
        "realtime_ready": True,
        "watchdog_ready": True,
    }
    values.update(updates)
    return RuntimeCapabilityManifest(**values)


def test_bundle_is_content_addressed_and_admitted_only_by_matching_runtime() -> None:
    bundle = _bundle()
    admission = admit_deployment_bundle(bundle, _runtime())

    assert bundle.bundle_id.startswith("sha256:")
    assert admission.admitted is True
    assert admission.blockers == ()
    reordered = DeploymentBundle.model_validate(
        {
            **bundle.model_dump(),
            "required_backend_capabilities": tuple(
                reversed(bundle.required_backend_capabilities)
            ),
        }
    )
    assert reordered.bundle_id == bundle.bundle_id


def test_bundle_admission_reports_every_field_blocker() -> None:
    admission = admit_deployment_bundle(
        _bundle(),
        _runtime(
            executor_abi="wrong",
            capabilities=(
                "external_object_pose",
                "observation_quality_guard",
                "task_goal_evaluator",
            ),
            approved_calibration_ids=(HASH_C,),
            maximum_force_n=5.0,
            maximum_speed_m_s=0.01,
            realtime_ready=False,
            watchdog_ready=False,
        ),
    )

    assert admission.admitted is False
    assert set(admission.blockers) == {
        "executor_abi",
        "missing_capability:cartesian_pose",
        "missing_capability:contact_monitor",
        "missing_capability:force_guard",
        "approved_calibration",
        "maximum_force_n",
        "maximum_speed_m_s",
        "realtime_ready",
        "watchdog_ready",
    }


def test_bundle_rejects_graph_binding_mismatch() -> None:
    bundle = _bundle()
    with pytest.raises(ValidationError, match="does not match"):
        DeploymentBundle.model_validate(
            {**bundle.model_dump(), "calibration_id": HASH_C}
        )


def test_bundle_rejects_omitted_graph_capability_and_runtime_duplicates() -> None:
    bundle = _bundle()
    with pytest.raises(ValidationError, match="omits graph capabilities"):
        DeploymentBundle.model_validate(
            {
                **bundle.model_dump(),
                "required_backend_capabilities": ("cartesian_pose",),
            }
        )
    with pytest.raises(ValidationError, match="runtime capabilities must be unique"):
        _runtime(capabilities=("cartesian_pose", "cartesian_pose"))
