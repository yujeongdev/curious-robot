import json

import pytest

from curious_robot.action_graph import (
    DeploymentBundle,
    DeploymentSafetyEnvelope,
    RuntimeCapabilityManifest,
    compile_motion_action_graph,
)
from curious_robot.cli import main

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def _graph():  # noqa: ANN202
    return compile_motion_action_graph(
        task_id="push-cube-v1",
        task_version="1.0.0",
        object_id="cube",
        object_archetype_version="1.0.0",
        geometry_hash=HASH_C,
        calibration_id=HASH_A,
        protocol_binding_id=HASH_B,
        phases=("approach", "push", "retreat"),
        phase_durations_s=(0.5, 1.0, 0.5),
        contact_expectations=("unexpected", "maintain", "release"),
        observation_timeout_s=0.25,
        wall_clock_deadline_s=4.0,
    )


def test_action_graph_validate_cli_reports_stable_identity(tmp_path, capsys) -> None:  # noqa: ANN001
    graph_path = tmp_path / "action-graph.json"
    graph_path.write_text(_graph().model_dump_json(indent=2))

    main(["action-graph-validate", "--graph", str(graph_path)])

    output = json.loads(capsys.readouterr().out)
    assert output["graph_id"] == _graph().graph_id
    assert output["nodes"] == len(_graph().nodes)


def test_bundle_validate_cli_fails_closed_with_runtime_blockers(tmp_path, capsys) -> None:  # noqa: ANN001
    graph = _graph()
    bundle = DeploymentBundle(
        bundle_name="cube-push",
        task_id="push-cube-v1",
        task_version="1.0.0",
        object_id="cube",
        object_archetype_version="1.0.0",
        geometry_hash=HASH_C,
        motion_plan_id=HASH_C,
        protocol_binding_id=HASH_B,
        calibration_id=HASH_A,
        executor_abi="franka-action-graph-v1",
        required_backend_capabilities=(
            "external_object_pose",
            "observation_quality_guard",
            "cartesian_pose",
            "force_guard",
            "contact_monitor",
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
    runtime = RuntimeCapabilityManifest(
        backend_id="field-host",
        executor_abi="franka-action-graph-v1",
        supported_graph_schema_versions=("action-graph-v1",),
        capabilities=(
            "external_object_pose",
            "observation_quality_guard",
            "cartesian_pose",
            "contact_monitor",
            "task_goal_evaluator",
        ),
        approved_calibration_ids=(HASH_A,),
        maximum_force_n=15.0,
        maximum_speed_m_s=0.1,
        realtime_ready=True,
        watchdog_ready=True,
    )
    bundle_path = tmp_path / "bundle.json"
    runtime_path = tmp_path / "runtime.json"
    bundle_path.write_text(bundle.model_dump_json(indent=2))
    runtime_path.write_text(runtime.model_dump_json(indent=2))

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "deployment-bundle-validate",
                "--bundle",
                str(bundle_path),
                "--runtime",
                str(runtime_path),
            ]
        )

    output = json.loads(capsys.readouterr().out)
    assert output["blockers"] == ["missing_capability:force_guard"]
