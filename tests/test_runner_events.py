import json

import pytest

from curious_robot.run_manager import RunConfiguration, RunManager


@pytest.mark.gpu
def test_real_episode_streams_typed_events_and_visual_frames(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED", "7")
    manager = RunManager(tmp_path)
    try:
        started = manager.start_run(
            RunConfiguration(seed=7, device="cuda:0", include_robot=True, recording=False)
        )
        terminal = manager.wait(started["run_id"], timeout_s=300)
        event_types = [event["type"] for event in manager.events_after(started["run_id"], 0)]
        assert terminal["state"] == "completed"
        assert "stage.entered" in event_types
        assert "belief.updated" in event_types
        assert "simulation.visual_frame" in event_types
        assert "action.selected" in event_types
        assert "verification.completed" in event_types
        assert event_types[-1] == "run.completed"
        run_directory = tmp_path / started["run_id"]
        provenance = json.loads((run_directory / "value_provenance.json").read_text())
        belief_config = json.loads((run_directory / "belief_simulation_config.json").read_text())
        reexecution = json.loads((run_directory / "reexecution.json").read_text())
        assert provenance["learned_physical_parameters"]["belief_world_input"] == (
            "belief_simulation_config.json"
        )
        assert belief_config["physical_parameters_source"] == "posterior"
        assert "evaluation_truth" not in belief_config
        assert belief_config["candidate_rollouts"]
        assert all(
            rollout["arguments"]["belief_revision"] == belief_config["belief_revision"]
            for rollout in belief_config["candidate_rollouts"]
        )
        assert all(
            rollout["arguments"]["world_config_id"] == rollout["outcome"]["world_config_id"]
            for rollout in belief_config["candidate_rollouts"]
        )
        for public_name in (
            "metadata.json",
            "episode_result.json",
            "decision_timeline.json",
            "experiment_protocol.json",
            "belief_simulation_config.json",
            "value_provenance.json",
        ):
            payload = json.loads((run_directory / public_name).read_text())
            assert not _contains_key(payload, "private_scenario_seed")
            assert not _contains_key(payload, "evaluation_truth")
        assert len(json.loads((run_directory / "metadata.json").read_text())["scenario_commitment"]) == 64
    finally:
        manager.close()


def _contains_key(value: object, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(_contains_key(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False
