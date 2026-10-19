import pytest

from curious_robot.run_manager import RunConfiguration, RunManager


@pytest.mark.gpu
def test_real_episode_streams_typed_events_and_visual_frames(tmp_path) -> None:
    manager = RunManager(tmp_path)
    try:
        started = manager.start_run(
            RunConfiguration(seed=7, device="cuda:0", include_robot=False, recording=False)
        )
        terminal = manager.wait(started["run_id"], timeout_s=180)
        event_types = [event["type"] for event in manager.events_after(started["run_id"], 0)]
        assert terminal["state"] == "completed"
        assert "stage.entered" in event_types
        assert "belief.updated" in event_types
        assert "simulation.visual_frame" in event_types
        assert "action.selected" in event_types
        assert "verification.completed" in event_types
        assert event_types[-1] == "run.completed"
    finally:
        manager.close()
