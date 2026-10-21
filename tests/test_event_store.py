from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from curious_robot.event_store import (
    EventStore,
    EventStoreCorruptionError,
    EventStoreError,
    EventStoreFrozenError,
    EventStoreWriterError,
)
from curious_robot.events import (
    BodyTransform,
    DiscoveryStage,
    EvaluationPayload,
    EvaluationRevealedEvent,
    RunCompletedEvent,
    RunPayload,
    RunStartedEvent,
    SimulationVisualFrameEvent,
    VisualFramePayload,
)


def common(run_id: UUID, sequence: int) -> dict[str, object]:
    return {
        "run_id": run_id,
        "sequence": sequence,
        "wall_time": datetime.now(UTC),
        "stage": DiscoveryStage.OBSERVE,
        "iteration_id": 0,
        "stage_instance_id": uuid4(),
    }


def started(run_id: UUID, sequence: int = 1) -> RunStartedEvent:
    return RunStartedEvent(**common(run_id, sequence), payload=RunPayload(state="running"))


def frame(
    run_id: UUID,
    sequence: int,
    world_id: UUID,
    frame_id: int,
    *,
    scenario_id: str | None = None,
) -> SimulationVisualFrameEvent:
    payload = VisualFramePayload(
        frame_id=frame_id,
        source_state_sequence=max(0, sequence - 1),
        scene="truth" if scenario_id is None else "belief",
        world_instance_id=world_id,
        scenario_id=scenario_id,
        body_manifest_id="truth-v1" if scenario_id is None else "belief-v1",
        sim_time_s=frame_id / 30,
        presentation_time_s=frame_id / 30,
        bodies=(
            BodyTransform(
                body_id="cube",
                label="unknown cube",
                asset_node_id="cube-node",
                position_m=(frame_id / 100, 0.0, 0.03),
                orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
            ),
        ),
    )
    return SimulationVisualFrameEvent(
        **common(run_id, sequence), frame_id=frame_id, sim_time_s=payload.sim_time_s, payload=payload
    )


def terminal(run_id: UUID, sequence: int) -> RunCompletedEvent:
    return RunCompletedEvent(
        **common(run_id, sequence), payload=RunPayload(state="completed", reason="verified")
    )


def test_append_returns_exact_committed_bytes_and_freezes_terminal_log(tmp_path: Path) -> None:
    run_id = uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        committed = store.append(started(run_id))
        assert committed.line == (tmp_path / "events.ndjson").read_bytes()
        assert committed.sse_data + b"\n" == committed.line

        store.append(terminal(run_id, 2))
        frozen_bytes = (tmp_path / "events.ndjson").read_bytes()
        assert store.frozen
        with pytest.raises(EventStoreFrozenError):
            store.append(started(run_id, 3))
        assert (tmp_path / "events.ndjson").read_bytes() == frozen_bytes


def test_late_frame_seek_uses_byte_index_and_preserves_exact_line(tmp_path: Path) -> None:
    run_id, world_id = uuid4(), uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        store.append(started(run_id))
        offsets = {}
        for index in range(1, 151):
            committed = store.append(frame(run_id, index + 1, world_id, index))
            offsets[index] = committed.offset

        sought = store.seek_visual_frame(world_id, 149)
        assert sought.offset == offsets[149]
        assert sought.event.payload.frame_id == 149
        with (tmp_path / "events.ndjson").open("rb") as source:
            source.seek(sought.offset)
            assert source.readline() == sought.line


def test_invalid_duplicate_frame_is_rejected_before_any_bytes_are_written(tmp_path: Path) -> None:
    run_id, world_id = uuid4(), uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        store.append(started(run_id))
        store.append(frame(run_id, 2, world_id, 1))
        before = (tmp_path / "events.ndjson").read_bytes()
        with pytest.raises(EventStoreError, match="duplicate visual frame"):
            store.append(frame(run_id, 3, world_id, 1))
        assert (tmp_path / "events.ndjson").read_bytes() == before
        assert store.last_sequence == 2


def test_recovery_discards_incomplete_suffix_and_rebuilds_visual_index(tmp_path: Path) -> None:
    run_id, world_id = uuid4(), uuid4()
    store = EventStore(tmp_path, run_id=run_id)
    store.append(started(run_id))
    first = store.append(frame(run_id, 2, world_id, 40))
    store.close()
    with (tmp_path / "events.ndjson").open("ab") as output:
        output.write(b'{"type":"simulation.visual_frame","cut":')
    (tmp_path / "visual_frames.index.ndjson").write_bytes(b"corrupt derived index\n")

    with EventStore(tmp_path, run_id=run_id) as recovered:
        assert recovered.last_sequence == 2
        assert recovered.seek_visual_frame(world_id, 40).line == first.line
        assert (tmp_path / "events.ndjson").read_bytes().endswith(first.line)
        assert b"corrupt derived index" not in (tmp_path / "visual_frames.index.ndjson").read_bytes()


def test_recovery_refuses_corrupt_newline_committed_record(tmp_path: Path) -> None:
    (tmp_path / "events.ndjson").write_bytes(b'{"not":"an event"}\n')
    with pytest.raises(EventStoreCorruptionError, match="invalid committed event"):
        EventStore(tmp_path)


def test_snapshot_contains_latest_complete_frame_for_each_world_scenario(tmp_path: Path) -> None:
    run_id, truth_world, belief_world = uuid4(), uuid4(), uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        store.append(started(run_id))
        store.append(frame(run_id, 2, truth_world, 1))
        store.append(frame(run_id, 3, belief_world, 1, scenario_id="push"))
        store.append(frame(run_id, 4, truth_world, 2))

        snapshot = store.snapshot(reducer_schema="1.0.0", state={"status": "paused"})
        assert snapshot.snapshot_sequence == 4
        frame_keys = {
            (item.payload.world_instance_id, item.payload.scenario_id, item.payload.frame_id)
            for item in snapshot.latest_visual_frames
        }
        assert frame_keys == {
            (truth_world, None, 2),
            (belief_world, "push", 1),
        }
        path = store.write_snapshot(snapshot)
        assert path.name == "000000000004.json"
        assert path.read_bytes().startswith(b'{"latest_visual_frames"')
        assert store.load_latest_snapshot() == snapshot
        assert store.load_latest_snapshot(at_or_before=3) is None


def test_snapshot_state_cannot_bypass_public_data_policy(tmp_path: Path) -> None:
    run_id = uuid4()
    with EventStore(tmp_path, run_id=run_id, forbidden_numeric_values=(712.75,)) as store:
        store.append(started(run_id))
        with pytest.raises(Exception, match="hidden_mass_kg"):
            store.snapshot(reducer_schema="1.0.0", state={"hidden_mass_kg": 0.4})
        with pytest.raises(Exception, match="sentinel"):
            store.snapshot(reducer_schema="1.0.0", state={"posterior": 712.75})


def test_public_policy_and_sequence_are_enforced_before_write(tmp_path: Path) -> None:
    run_id = uuid4()
    with EventStore(tmp_path, run_id=run_id, forbidden_numeric_values=(901.25,)) as store:
        store.append(started(run_id))
        invalid = RunStartedEvent(**common(run_id, 2), payload=RunPayload(state="running", suspicious=901.25))
        with pytest.raises(Exception, match="sentinel"):
            store.append(invalid)
        with pytest.raises(EventStoreError, match="sequence must be 2"):
            store.append(started(run_id, 3))


def test_only_creating_thread_can_mutate_store(tmp_path: Path) -> None:
    run_id = uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        errors: list[Exception] = []

        def other_writer() -> None:
            try:
                store.append(started(run_id))
            except Exception as exc:  # the exception is the assertion target
                errors.append(exc)

        thread = threading.Thread(target=other_writer)
        thread.start()
        thread.join()
        assert len(errors) == 1
        assert isinstance(errors[0], EventStoreWriterError)


def test_evaluator_reveal_is_separate_and_only_post_terminal(tmp_path: Path) -> None:
    run_id = uuid4()
    with EventStore(tmp_path, run_id=run_id) as store:
        store.append(started(run_id))
        reveal = EvaluationRevealedEvent(
            **common(run_id, 3), payload=EvaluationPayload(comparison={"mass_error_kg": 0.01})
        )
        with pytest.raises(EventStoreError, match="only after"):
            store.append_evaluator_event(reveal)
        store.append(terminal(run_id, 2))
        canonical = (tmp_path / "events.ndjson").read_bytes()
        evaluator_line = store.append_evaluator_event(reveal)
        assert (tmp_path / "events.ndjson").read_bytes() == canonical
        assert (tmp_path / "evaluator_events.ndjson").read_bytes() == evaluator_line
