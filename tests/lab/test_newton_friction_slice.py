from __future__ import annotations

import json

import pytest

from curious_robot.lab.artifact_store import LaboratoryArtifactStores
from curious_robot.lab.contracts import ArtifactEnvelope
from curious_robot.lab.friction_runner import run_newton_friction_slice
from curious_robot.lab.newton_ports import TerminalEvaluator
from curious_robot.lab.process_port import PortProcessError


def test_terminal_evaluator_requires_completed_controller(tmp_path) -> None:
    stores = LaboratoryArtifactStores(tmp_path)
    deployment = stores.agent.put(
        ArtifactEnvelope(
            kind="reality_deployment",
            payload={
                "action_id": "push_straight_low",
                "attempt_count": 1,
                "attempts": [
                    {"controller_outcome": "travel_exhausted", "target_error_m": 0.0}
                ],
                "controller_outcome": "travel_exhausted",
                "fresh_episode": True,
                "observed_final_position_m": [0.15, 0.0, 0.025],
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

    dynamic = next(
        experiment
        for experiment in dataset["experiments"]
        if experiment["experiment"] == "dynamic_friction"
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
