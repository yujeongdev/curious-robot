from __future__ import annotations

import json

import pytest

from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.capability_artifact import distill_friction_capability
from curious_robot.lab.capability_skill import SkillStatus, execute_from_manifest
from curious_robot.lab.contracts import ArtifactEnvelope
from curious_robot.lab.friction_runner import run_newton_friction_slice
from curious_robot.lab.newton_ports import TerminalEvaluator
from curious_robot.lab.process_port import PortProcessError
from curious_robot.objects import T_BAR_V1


def test_terminal_evaluator_requires_completed_controller(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    deployment = stores.agent.put(
        ArtifactEnvelope(
            kind="reality_deployment",
            payload={
                "action_id": "push_straight_low",
                "attempt_count": 1,
                "attempts": [{"controller_outcome": "travel_exhausted", "target_error_m": 0.0}],
                "controller_outcome": "travel_exhausted",
                "fresh_episode": True,
                "observed_final_position_m": [0.15, 0.0, 0.025],
                "peak_contact_force_n": 1.0,
                "prediction_target_error_m": 0.0,
                "runtime_process_id": 101,
                "safety_tripped": False,
                "target_position_m": [0.15, 0.0, 0.025],
                "target_radius_m": 0.06,
                "terminal": True,
                "world_instance_id": "fresh-world",
            },
            validation_conditions=("terminal deployment outcome is recorded",),
        )
    )
    evaluator = TerminalEvaluator(tmp_path, timeout_s=10)
    try:
        report = stores.evaluator.get(evaluator.evaluate_terminal(deployment)).payload
    finally:
        evaluator.close()

    assert report["goal_predicates"]["cube_in_target"] is True
    assert report["goal_predicates"]["controller_completed"] is False
    assert report["success"] is False


def test_terminal_evaluator_rejects_wrong_kind_lookalike(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    lookalike = stores.agent.put(
        ArtifactEnvelope(
            kind="unrelated_note",
            payload={
                "controller_outcome": "completed",
                "fresh_episode": True,
                "observed_final_position_m": [0.15, 0.0, 0.025],
                "safety_tripped": False,
                "target_position_m": [0.15, 0.0, 0.025],
                "target_radius_m": 0.06,
                "terminal": True,
            },
            validation_conditions=("note schema valid",),
        )
    )
    evaluator = TerminalEvaluator(tmp_path, timeout_s=10)
    try:
        with pytest.raises(PortProcessError, match="ValueError"):
            evaluator.evaluate_terminal(lookalike)
    finally:
        evaluator.close()


def test_terminal_evaluator_rejects_tbar_position_success_with_wrong_yaw(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    deployment = stores.agent.put(
        ArtifactEnvelope(
            kind="reality_deployment",
            payload={
                "action_id": "push_tbar_nominal",
                "attempt_count": 1,
                "attempts": [
                    {
                        "controller_outcome": "completed",
                        "target_error_m": 0.0,
                        "yaw_error_rad": 0.17453292519943295,
                    }
                ],
                "controller_outcome": "completed",
                "fresh_episode": True,
                "task_id": "push-t-bar-v1",
                "task_version": "1.0.0",
                "object_id": "t-bar",
                "object_archetype_version": "1.0.0",
                "geometry_hash": T_BAR_V1.geometry_hash,
                "observed_final_position_m": [0.15, 0.0, 0.02],
                "observed_final_yaw_rad": 0.17453292519943295,
                "peak_contact_force_n": 1.0,
                "prediction_target_error_m": 0.0,
                "runtime_process_id": 101,
                "safety_tripped": False,
                "target_position_m": [0.15, 0.0, 0.02],
                "target_radius_m": 0.03,
                "target_yaw_rad": 0.0,
                "yaw_tolerance_rad": 0.08726646259971647,
                "terminal": True,
                "world_instance_id": "fresh-tbar-world",
            },
            validation_conditions=("terminal deployment outcome is recorded",),
        )
    )
    evaluator = TerminalEvaluator(tmp_path, timeout_s=10)
    try:
        report = stores.evaluator.get(evaluator.evaluate_terminal(deployment)).payload
    finally:
        evaluator.close()

    assert report["goal_predicates"]["position_within_tolerance"] is True
    assert report["goal_predicates"]["yaw_within_tolerance"] is False
    assert report["goal_predicates"]["t_bar_in_target_pose"] is False
    assert report["success"] is False


@pytest.mark.gpu
def test_newton_friction_slice_uses_independent_twin_and_fresh_reality(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED", "7")
    result = run_newton_friction_slice(
        output_directory=tmp_path,
        device="cuda:0",
        seed=7,
    )
    stores = LaboratoryArtifactStores(tmp_path)
    dataset = stores.agent.get(result.trajectory_dataset).payload
    posterior = stores.agent.get(result.parameter_posterior).payload
    twin_build = stores.agent.get(result.twin_build).payload
    twin_validation = stores.agent.get(result.twin_validation_report).payload
    deployment = stores.agent.get(result.reality_deployment).payload
    evaluation = stores.evaluator.get(result.evaluation_report).payload
    manifest = json.loads((tmp_path / "friction_capability_manifest.json").read_text())
    assert manifest["status"] == "PASSED"

    dynamic = next(
        experiment for experiment in dataset["experiments"] if experiment["experiment"] == "dynamic_friction"
    )
    assert dynamic["experiment"] == "dynamic_friction"
    assert any(sample["contact_valid"] for sample in dynamic["samples"])
    evidence_ids = posterior["physical_belief"]["supporting_evidence_ids"]
    assert evidence_ids == [experiment["evidence_id"] for experiment in dataset["experiments"]]
    assert twin_build["world_instance_id"] != dataset["world_instance_id"]
    assert all(
        outcome["world_instance_id"] == twin_build["world_instance_id"]
        for outcome in twin_validation["outcomes"]
    )
    assert deployment["fresh_episode"] is True
    assert deployment["world_instance_id"] != dataset["world_instance_id"]
    assert evaluation["terminal"] is True

    identities = manifest["runtime_identities"]
    assert len({identity["process_id"] for identity in identities.values()}) == 3
    agent_documents = "\n".join(path.read_text() for path in (tmp_path / "agent").rglob("*.json"))
    for forbidden in ("scenario_seed", "evaluation_truth", "hidden_parameters"):
        assert forbidden not in agent_documents

    capability_ref = distill_friction_capability(stores, result)
    (tmp_path / "capability_artifact.json").write_text(
        json.dumps({"artifact_id": capability_ref.artifact_id})
    )
    skill_result = execute_from_manifest(tmp_path, device="cuda:0", seed=7)
    assert skill_result.status is SkillStatus.COMPLETED


@pytest.mark.gpu
def test_tbar_slice_runs_three_independent_twins_and_pose_evaluation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED", "7")
    result = run_newton_friction_slice(
        output_directory=tmp_path,
        device="cuda:0",
        seed=7,
        task_id="push-t-bar-v1",
    )
    stores = LaboratoryArtifactStores(tmp_path)
    dataset = stores.agent.get(result.trajectory_dataset).payload
    validation = stores.agent.get(result.twin_validation_report).payload
    deployment = stores.agent.get(result.reality_deployment).payload
    evaluation = stores.evaluator.get(result.evaluation_report).payload

    assert dataset["task"]["object_id"] == "t-bar"
    assert len(dataset["public_geometry"]["parts"]) == 2
    assert len(validation["outcomes"]) == 3
    candidate_worlds = {outcome["world_instance_id"] for outcome in validation["outcomes"]}
    assert len(candidate_worlds) == 3
    assert deployment["world_instance_id"] not in candidate_worlds
    assert deployment["world_instance_id"] != dataset["world_instance_id"]
    assert deployment["geometry_hash"] == dataset["task"]["geometry_hash"]
    assert evaluation["goal_predicates"]["position_within_tolerance"] is True
    assert evaluation["goal_predicates"]["yaw_within_tolerance"] is True
    assert evaluation["success"] is True
    with pytest.raises(ValueError, match="fail-closed"):
        distill_friction_capability(stores, result)

    agent_documents = "\n".join(
        path.read_text() for path in (tmp_path / "agent").rglob("*.json")
    )
    for forbidden in ("scenario_seed", "evaluation_truth", "hidden_parameters"):
        assert forbidden not in agent_documents
