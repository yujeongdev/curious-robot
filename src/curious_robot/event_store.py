"""Durable, append-only storage for Agent Lab events.

The manager process is expected to own one :class:`EventStore`.  The store
enforces that expectation at the thread boundary, preserves the exact bytes
that callers publish over SSE, and maintains a byte-offset visual-frame index
for direct replay seeks.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field

from curious_robot.events import (
    TERMINAL_EVENT_TYPES,
    AgentEvent,
    EvaluationRevealedEvent,
    PublicModel,
    SimulationVisualFrameEvent,
    parse_event,
    serialize_event,
    validate_public_data,
    validate_public_event,
)

MAX_EVENT_BYTES = 512 * 1024


class EventStoreError(RuntimeError):
    """Base class for persistence contract failures."""


class EventStoreCorruptionError(EventStoreError):
    """A newline-committed record is invalid and cannot be ignored."""


class EventStoreFrozenError(EventStoreError):
    """The canonical log was terminalized and cannot change."""


class EventStoreWriterError(EventStoreError):
    """A caller other than the creating writer attempted to mutate the store."""


class VisualFrameKey(PublicModel):
    world_instance_id: UUID
    scenario_id: str | None = None


class EventStoreSnapshot(PublicModel):
    snapshot_sequence: int = Field(ge=0)
    reducer_schema: str
    run_id: UUID
    state: dict[str, Any]
    latest_visual_frames: tuple[SimulationVisualFrameEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CommittedEvent:
    event: AgentEvent
    offset: int
    line: bytes

    @property
    def sse_data(self) -> bytes:
        """The exact committed JSON bytes, excluding only the NDJSON newline."""

        return self.line[:-1]


@dataclass(frozen=True, slots=True)
class VisualIndexEntry:
    world_instance_id: UUID
    frame_id: int
    sequence: int
    offset: int

    def as_json_line(self) -> bytes:
        value = {
            "frame_id": self.frame_id,
            "offset": self.offset,
            "sequence": self.sequence,
            "world_instance_id": str(self.world_instance_id),
        }
        return json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n"


class EventStore:
    """Single-writer NDJSON log with recovery, snapshots and frame seeks."""

    def __init__(
        self,
        directory: str | Path,
        *,
        run_id: UUID | None = None,
        forbidden_numeric_values: tuple[float, ...] = (),
        recover: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / "events.ndjson"
        self.index_path = self.directory / "visual_frames.index.ndjson"
        self.snapshots_directory = self.directory / "snapshots"
        self.snapshots_directory.mkdir(exist_ok=True)
        self.evaluator_path = self.directory / "evaluator_events.ndjson"
        self._run_id = run_id
        self._forbidden_numeric_values = forbidden_numeric_values
        self._writer_thread = threading.get_ident()
        self._lock = threading.RLock()
        self._frame_index: dict[tuple[UUID, int], VisualIndexEntry] = {}
        self._latest_frames: dict[tuple[UUID, str | None], SimulationVisualFrameEvent] = {}
        self._last_sequence = 0
        self._frozen = False
        self._terminal_type: str | None = None

        self._events_file = self.events_path.open("a+b")
        self._index_file = self.index_path.open("a+b")
        if recover:
            self._recover()
        elif self.events_path.stat().st_size:
            raise EventStoreError("refusing to open a non-empty store without recovery")

    @property
    def run_id(self) -> UUID | None:
        return self._run_id

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def terminal_type(self) -> str | None:
        return self._terminal_type

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _assert_writer(self) -> None:
        if threading.get_ident() != self._writer_thread:
            raise EventStoreWriterError("EventStore mutations are restricted to its creating writer thread")

    def _recover(self) -> None:
        """Discard only an incomplete suffix and rebuild all derived indexes."""

        self._assert_writer()
        self._events_file.flush()
        self._events_file.seek(0)
        offset = 0
        truncate_at: int | None = None
        while True:
            line = self._events_file.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                truncate_at = offset
                break
            try:
                event = parse_event(line[:-1])
                validate_public_event(event, forbidden_numeric_values=self._forbidden_numeric_values)
                self._accept_recovered(event, offset)
            except Exception as exc:
                raise EventStoreCorruptionError(
                    f"invalid committed event at byte offset {offset}: {exc}"
                ) from exc
            offset += len(line)

        if truncate_at is not None:
            self._events_file.seek(truncate_at)
            self._events_file.truncate()
            self._events_file.flush()
            os.fsync(self._events_file.fileno())

        self._rewrite_index()
        self._events_file.seek(0, os.SEEK_END)

    def _accept_recovered(self, event: AgentEvent, offset: int) -> None:
        if self._frozen:
            raise EventStoreCorruptionError("event exists after the terminal event")
        if self._run_id is None:
            self._run_id = event.run_id
        if event.run_id != self._run_id:
            raise EventStoreCorruptionError("run_id changed inside one event log")
        expected = self._last_sequence + 1
        if event.sequence != expected:
            raise EventStoreCorruptionError(
                f"event sequence gap: expected {expected}, found {event.sequence}"
            )
        self._last_sequence = event.sequence
        if event.type == "simulation.visual_frame":
            self._validate_visual(event)
            self._record_visual(event, offset)
        if event.type in TERMINAL_EVENT_TYPES:
            self._frozen = True
            self._terminal_type = event.type

    def append(self, event: AgentEvent, *, fsync: bool | None = None) -> CommittedEvent:
        """Validate and atomically commit one complete NDJSON record.

        The returned ``line`` is the authoritative byte string for live
        publication.  Consumers must not serialize the model a second time.
        """

        self._assert_writer()
        with self._lock:
            if self._frozen:
                raise EventStoreFrozenError(f"event store is frozen by {self._terminal_type}")
            validate_public_event(event, forbidden_numeric_values=self._forbidden_numeric_values)
            if self._run_id is None:
                self._run_id = event.run_id
            if event.run_id != self._run_id:
                raise EventStoreError("event run_id does not match this store")
            expected = self._last_sequence + 1
            if event.sequence != expected:
                raise EventStoreError(f"event sequence must be {expected}, got {event.sequence}")
            if event.type == "simulation.visual_frame":
                self._validate_visual(event)

            line = serialize_event(event) + b"\n"
            if len(line) > MAX_EVENT_BYTES:
                raise EventStoreError(f"event exceeds {MAX_EVENT_BYTES} byte limit")

            self._events_file.seek(0, os.SEEK_END)
            offset = self._events_file.tell()
            written = self._events_file.write(line)
            if written != len(line):
                raise EventStoreError(f"short event write: expected {len(line)} bytes, wrote {written}")
            self._events_file.flush()

            self._last_sequence = event.sequence
            if event.type == "simulation.visual_frame":
                self._record_visual(event, offset)
                entry = self._frame_index[(event.payload.world_instance_id, event.payload.frame_id)]
                self._index_file.seek(0, os.SEEK_END)
                self._index_file.write(entry.as_json_line())
                self._index_file.flush()

            boundary = event.type in {"stage.completed", *TERMINAL_EVENT_TYPES}
            if fsync is True or (fsync is None and boundary):
                os.fsync(self._events_file.fileno())
                if event.type == "simulation.visual_frame":
                    os.fsync(self._index_file.fileno())

            if event.type in TERMINAL_EVENT_TYPES:
                self._terminal_type = event.type
                self._frozen = True
            return CommittedEvent(event=event, offset=offset, line=line)

    def _record_visual(self, event: SimulationVisualFrameEvent, offset: int) -> None:
        payload = event.payload
        index_key = (payload.world_instance_id, payload.frame_id)
        entry = VisualIndexEntry(
            world_instance_id=payload.world_instance_id,
            frame_id=payload.frame_id,
            sequence=event.sequence,
            offset=offset,
        )
        self._frame_index[index_key] = entry
        latest_key = (payload.world_instance_id, payload.scenario_id)
        self._latest_frames[latest_key] = event

    def _validate_visual(self, event: SimulationVisualFrameEvent) -> None:
        payload = event.payload
        index_key = (payload.world_instance_id, payload.frame_id)
        if index_key in self._frame_index:
            raise EventStoreError(
                f"duplicate visual frame {payload.frame_id} for world {payload.world_instance_id}"
            )
        latest_key = (payload.world_instance_id, payload.scenario_id)
        previous = self._latest_frames.get(latest_key)
        if previous is not None and payload.frame_id <= previous.payload.frame_id:
            raise EventStoreError("visual frame_id must increase within a world/scenario")

    def _rewrite_index(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        with temporary.open("wb") as output:
            for entry in sorted(self._frame_index.values(), key=lambda item: item.sequence):
                output.write(entry.as_json_line())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.index_path)
        self._index_file.close()
        self._index_file = self.index_path.open("a+b")

    def seek_visual_frame(self, world_instance_id: UUID, frame_id: int) -> CommittedEvent:
        """Seek directly to an indexed frame without scanning preceding events."""

        with self._lock:
            entry = self._frame_index.get((world_instance_id, frame_id))
            if entry is None:
                raise KeyError(f"unknown visual frame {world_instance_id}/{frame_id}")
            self._events_file.flush()
            self._events_file.seek(entry.offset)
            line = self._events_file.readline()
            if not line.endswith(b"\n"):
                raise EventStoreCorruptionError("indexed visual frame is not a complete line")
            event = parse_event(line[:-1])
            if event.type != "simulation.visual_frame":
                raise EventStoreCorruptionError("visual index points to a non-frame event")
            if event.sequence != entry.sequence:
                raise EventStoreCorruptionError("visual index sequence does not match stored frame")
            return CommittedEvent(event=event, offset=entry.offset, line=line)

    def read_event(self, sequence: int) -> CommittedEvent:
        """Read a sequence for diagnostics; visual replay should use its index."""

        if sequence < 1 or sequence > self._last_sequence:
            raise KeyError(sequence)
        with self._lock:
            self._events_file.flush()
            self._events_file.seek(0)
            offset = 0
            for line in self._events_file:
                event = parse_event(line[:-1])
                if event.sequence == sequence:
                    return CommittedEvent(event=event, offset=offset, line=line)
                offset += len(line)
        raise EventStoreCorruptionError(f"sequence {sequence} was indexed by range but not found")

    def snapshot(self, *, reducer_schema: str, state: dict[str, Any]) -> EventStoreSnapshot:
        """Create an atomic in-memory snapshot under the store lock."""

        with self._lock:
            if self._run_id is None:
                raise EventStoreError("cannot snapshot an empty store")
            validate_public_data(state, forbidden_numeric_values=self._forbidden_numeric_values)
            return EventStoreSnapshot(
                snapshot_sequence=self._last_sequence,
                reducer_schema=reducer_schema,
                run_id=self._run_id,
                state=state,
                latest_visual_frames=tuple(
                    sorted(self._latest_frames.values(), key=lambda event: event.sequence)
                ),
            )

    def write_snapshot(self, snapshot: EventStoreSnapshot) -> Path:
        """Durably publish a snapshot with rename-after-fsync semantics."""

        self._assert_writer()
        with self._lock:
            validate_public_data(snapshot, forbidden_numeric_values=self._forbidden_numeric_values)
            if snapshot.run_id != self._run_id:
                raise EventStoreError("snapshot run_id does not match this store")
            if snapshot.snapshot_sequence > self._last_sequence:
                raise EventStoreError("snapshot cannot include a future sequence")
            latest_expected = {
                (event.payload.world_instance_id, event.payload.scenario_id): event.sequence
                for event in self._latest_frames.values()
                if event.sequence <= snapshot.snapshot_sequence
            }
            latest_given = {
                (event.payload.world_instance_id, event.payload.scenario_id): event.sequence
                for event in snapshot.latest_visual_frames
            }
            if latest_given != latest_expected:
                raise EventStoreError(
                    "snapshot must contain the complete latest visual frame per world/scenario"
                )
            path = self.snapshots_directory / f"{snapshot.snapshot_sequence:012d}.json"
            temporary = path.with_suffix(".tmp")
            data = json.dumps(
                snapshot.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
            with temporary.open("wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            return path

    def load_latest_snapshot(self, *, at_or_before: int | None = None) -> EventStoreSnapshot | None:
        """Load the newest valid reducer checkpoint at or before a sequence.

        Snapshot corruption is reported instead of silently replaying from an
        older state: a corrupt checkpoint is authoritative evidence of a
        storage problem, while absence simply means replay starts at event 1.
        """

        with self._lock:
            limit = self._last_sequence if at_or_before is None else at_or_before
            candidates = sorted(self.snapshots_directory.glob("*.json"), reverse=True)
            for path in candidates:
                try:
                    sequence = int(path.stem)
                except ValueError:
                    continue
                if sequence > limit:
                    continue
                try:
                    snapshot = EventStoreSnapshot.model_validate_json(path.read_bytes())
                    validate_public_data(snapshot, forbidden_numeric_values=self._forbidden_numeric_values)
                except Exception as exc:
                    raise EventStoreCorruptionError(f"invalid snapshot {path.name}: {exc}") from exc
                if snapshot.snapshot_sequence != sequence:
                    raise EventStoreCorruptionError(
                        f"snapshot filename sequence {sequence} does not match payload "
                        f"{snapshot.snapshot_sequence}"
                    )
                if snapshot.run_id != self._run_id:
                    raise EventStoreCorruptionError("snapshot run_id does not match event log")
                return snapshot
            return None

    def append_evaluator_event(self, event: EvaluationRevealedEvent) -> bytes:
        """Write evaluator-only reveal data after canonical terminalization.

        This is intentionally a separate file: terminal public NDJSON bytes are
        immutable once downloadable.
        """

        self._assert_writer()
        with self._lock:
            if not self._frozen:
                raise EventStoreError("evaluation reveal is legal only after a terminal event")
            if event.run_id != self._run_id:
                raise EventStoreError("evaluator event run_id does not match this store")
            line = serialize_event(event) + b"\n"
            with self.evaluator_path.open("ab") as output:
                output.write(line)
                output.flush()
                os.fsync(output.fileno())
            return line

    def close(self) -> None:
        with self._lock:
            if not self._events_file.closed:
                self._events_file.flush()
                self._events_file.close()
            if not self._index_file.closed:
                self._index_file.flush()
                self._index_file.close()


__all__ = [
    "CommittedEvent",
    "EventStore",
    "EventStoreCorruptionError",
    "EventStoreError",
    "EventStoreFrozenError",
    "EventStoreSnapshot",
    "EventStoreWriterError",
    "MAX_EVENT_BYTES",
    "VisualFrameKey",
]
