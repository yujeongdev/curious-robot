from __future__ import annotations

import time
from pathlib import Path

import pytest

from curious_robot.run_manager import (
    ActiveRunError,
    CursorError,
    InvalidRunStateError,
    RunConfiguration,
    RunManager,
    RunNotFoundError,
)

FIXTURE_WORKER = "curious_robot.run_manager:fixture_worker"
PUBLIC_ARTIFACT_ID = "a9a83db5-aadb-4f96-8074-b76301e29f6e"
EVALUATOR_ARTIFACT_ID = "584788d7-3546-43a6-b2d7-82fd9b6285ad"


@pytest.fixture
def manager(tmp_path: Path):
    value = RunManager(
        tmp_path,
        worker_entrypoint=FIXTURE_WORKER,
        cancel_grace_s=0.5,
        terminate_grace_s=0.5,
    )
    yield value
    value.close()


def wait_state(manager: RunManager, run_id: str, state: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(run_id)
        if snapshot["state"] == state:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"run never reached {state}: {manager.snapshot(run_id)}")


def test_spawn_worker_commits_ordered_terminal_trace(manager: RunManager) -> None:
    started = manager.start_run(
        RunConfiguration(material_bounds={"fixture_steps": 3, "fixture_delay_s": 0.01})
    )
    run_id = started["run_id"]

    terminal = manager.wait(run_id, 8)
    events = manager.events_after(run_id, 0)

    assert terminal["state"] == "completed"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"
    assert sum(event["type"] == "simulation.visual_frame" for event in events) == 3
    assert manager.ndjson(run_id).count(b"\n") == len(events)


def test_one_active_run_and_future_cursor_are_rejected(manager: RunManager) -> None:
    first = manager.start_run(
        RunConfiguration(material_bounds={"fixture_steps": 100, "fixture_delay_s": 0.02})
    )
    with pytest.raises(ActiveRunError) as exc_info:
        manager.start_run(RunConfiguration())
    assert exc_info.value.run_id == first["run_id"]
    with pytest.raises(CursorError):
        manager.events_after(first["run_id"], 100_000)

    manager.cancel(first["run_id"])
    assert manager.wait(first["run_id"], 8)["state"] == "cancelled"


def test_pause_step_resume_and_cancel_are_safe_and_idempotent(manager: RunManager) -> None:
    snapshot = manager.start_run(
        RunConfiguration(material_bounds={"fixture_steps": 200, "fixture_delay_s": 0.03})
    )
    run_id = snapshot["run_id"]
    wait_state(manager, run_id, "running")

    assert manager.pause(run_id)["state"] == "pause_pending"
    paused = wait_state(manager, run_id, "paused")
    assert manager.pause(run_id)["state"] == "paused"
    frame_count = sum(
        event["type"] == "simulation.visual_frame"
        for event in manager.events_after(run_id, 0)
    )

    assert manager.step(run_id)["display_substeps"] == 8
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        after = sum(
            event["type"] == "simulation.visual_frame"
            for event in manager.events_after(run_id, 0)
        )
        if after == frame_count + 1:
            break
        time.sleep(0.01)
    assert after == frame_count + 1
    assert manager.snapshot(run_id)["state"] == "paused"
    assert manager.snapshot(run_id)["snapshot_sequence"] > paused["snapshot_sequence"]

    assert manager.resume(run_id)["state"] == "resume_pending"
    wait_state(manager, run_id, "running")
    assert manager.resume(run_id)["state"] == "running"
    assert manager.cancel(run_id)["state"] == "cancelling"
    assert manager.cancel(run_id)["state"] == "cancelling"
    assert manager.wait(run_id, 8)["state"] == "cancelled"
    with pytest.raises(InvalidRunStateError):
        manager.step(run_id)


def test_worker_failure_preserves_committed_prefix(manager: RunManager) -> None:
    run_id = manager.start_run(
        RunConfiguration(material_bounds={"fixture_fail": True})
    )["run_id"]

    terminal = manager.wait(run_id, 8)
    events = manager.events_after(run_id, 0)

    assert terminal["state"] == "failed"
    assert [event["type"] for event in events] == ["run.started", "run.failed"]
    assert events[-1]["payload"]["error_type"] == "RuntimeError"
    assert b"requested fixture failure" in manager.ndjson(run_id)


def test_abrupt_worker_exit_becomes_a_terminal_failure(manager: RunManager) -> None:
    run_id = manager.start_run(
        RunConfiguration(material_bounds={"fixture_crash": True})
    )["run_id"]

    terminal = manager.wait(run_id, 8)
    events = manager.events_after(run_id, 0)

    assert terminal["state"] == "failed"
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["payload"]["error_type"] == "WorkerExited"
    assert events[-1]["payload"]["message"].endswith("17")


def test_canonical_store_bytes_are_the_stream_source(manager: RunManager) -> None:
    run_id = manager.start_run(
        RunConfiguration(material_bounds={"fixture_steps": 1})
    )["run_id"]
    manager.wait(run_id, 8)

    committed = manager.committed_after(run_id, 0)
    canonical = manager.ndjson(run_id)

    assert canonical == b"".join(line + b"\n" for _, line in committed)
    assert canonical == (manager.artifact_root / run_id / "events.ndjson").read_bytes()


def test_artifacts_are_opaque_allowlisted_and_evaluator_gated(tmp_path: Path) -> None:
    disabled = RunManager(tmp_path / "disabled", worker_entrypoint=FIXTURE_WORKER)
    try:
        run_id = disabled.start_run(
            RunConfiguration(material_bounds={"fixture_artifacts": True})
        )["run_id"]
        disabled.wait(run_id, 8)
        path, _ = disabled.artifact(run_id, PUBLIC_ARTIFACT_ID)
        assert path.read_text() == "public fixture\n"
        with pytest.raises(PermissionError):
            disabled.artifact(run_id, EVALUATOR_ARTIFACT_ID)
        with pytest.raises(RunNotFoundError):
            disabled.artifact(run_id, "../evaluator.txt")
    finally:
        disabled.close()

    enabled = RunManager(tmp_path / "enabled", worker_entrypoint=FIXTURE_WORKER)
    try:
        run_id = enabled.start_run(
            RunConfiguration(
                evaluator_mode=True,
                material_bounds={"fixture_artifacts": True},
            )
        )["run_id"]
        enabled.wait(run_id, 8)
        path, _ = enabled.artifact(run_id, EVALUATOR_ARTIFACT_ID)
        assert path.read_text() == "evaluator fixture\n"
    finally:
        enabled.close()
