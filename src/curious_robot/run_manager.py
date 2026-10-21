"""Spawned Newton worker lifecycle and deterministic run controls.

The HTTP process owns event sequencing and public state.  A worker receives
commands and emits *unsequenced* intents over separate one-way Pipes.  Keeping
this module free of FastAPI and Newton imports makes lifecycle behavior usable
in CPU-only tests and lets the child initialize CUDA after ``spawn``.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import inspect
import json
import multiprocessing as mp
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .event_store import EventStore
from .events import EVENT_ADAPTER, AgentEvent, DiscoveryStage, validate_public_data

MAX_IPC_MESSAGE_BYTES = 512 * 1024
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "abandoned"})
CONTROLLABLE_STATES = frozenset({"initializing", "running", "pause_pending", "paused", "resume_pending"})


class RunManagerError(RuntimeError):
    """Base exception for manager contract failures."""


class ActiveRunError(RunManagerError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} is already active")
        self.run_id = run_id


class RunNotFoundError(RunManagerError):
    pass


class InvalidRunStateError(RunManagerError):
    pass


class CursorError(RunManagerError):
    """The client cursor cannot be continued safely."""


class RunCancelled(RuntimeError):
    """Raised by :class:`ControlGate` at a safe controller boundary."""


class EventRepository(Protocol):
    """Optional durable sink owned by the manager process.

    The repository receives the exact committed event dictionary.  A durable
    EventStore adapter can implement this protocol without the worker knowing
    anything about persistence or HTTP.
    """

    def append(self, event: AgentEvent) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class RunConfiguration:
    seed: int = 7
    device: str = "cuda:0"
    include_robot: bool = True
    recording: bool = True
    evaluator_mode: bool = False
    material_bounds: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RunConfiguration:
        known = {
            "seed": int(value.get("seed", 7)),
            "device": str(value.get("device", "cuda:0")),
            "include_robot": bool(value.get("include_robot", True)),
            "recording": bool(value.get("recording", True)),
            "evaluator_mode": bool(value.get("evaluator_mode", False)),
            "material_bounds": dict(value.get("material_bounds", {})),
        }
        return cls(**known)

    def public_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "device": self.device,
            "include_robot": self.include_robot,
            "recording": self.recording,
            "material_bounds": dict(self.material_bounds),
        }


class PipeEventSink:
    """Worker-side sink that accepts mappings or Pydantic models."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def emit(self, intent: Mapping[str, Any] | Any) -> None:
        value = intent.model_dump(mode="json") if hasattr(intent, "model_dump") else dict(intent)
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_IPC_MESSAGE_BYTES:
            raise ValueError(f"worker intent exceeds {MAX_IPC_MESSAGE_BYTES} bytes")
        self._connection.send_bytes(raw)


class ControlGate:
    """Worker-side pause/step/cancel gate checked before each physics step.

    ``step`` releases exactly ``display_substeps`` calls to ``before_step`` and
    then blocks again.  It consumes no random numbers and observes no sensors.
    """

    def __init__(
        self,
        command_connection: Any,
        event_sink: PipeEventSink,
        *,
        display_substeps: int = 8,
        poll_interval_s: float = 0.01,
    ) -> None:
        if display_substeps < 1:
            raise ValueError("display_substeps must be positive")
        self._commands = command_connection
        self._events = event_sink
        self._display_substeps = display_substeps
        self._poll_interval_s = poll_interval_s
        self._paused = False
        self._step_remaining = 0
        self._cancelled = False

    @property
    def paused(self) -> bool:
        return self._paused

    def checkpoint(self) -> None:
        """Process pending commands without advancing physics."""
        self._drain_commands()
        if self._cancelled:
            raise RunCancelled("run cancelled at a safe checkpoint")

    def before_step(self) -> None:
        while True:
            self._drain_commands()
            if self._cancelled:
                raise RunCancelled("run cancelled at a safe physics boundary")
            if not self._paused:
                return
            if self._step_remaining:
                self._step_remaining -= 1
                return
            if self._commands.poll(self._poll_interval_s):
                continue

    def _drain_commands(self) -> None:
        while self._commands.poll(0):
            command = json.loads(self._commands.recv_bytes())
            kind = command.get("command")
            if kind == "pause" and not self._paused:
                self._paused = True
                self._step_remaining = 0
                self._events.emit({"type": "run.paused", "payload": {"state": "paused"}})
            elif kind == "resume" and self._paused:
                self._paused = False
                self._step_remaining = 0
                self._events.emit({"type": "run.resumed", "payload": {"state": "running"}})
            elif kind == "step" and self._paused and self._step_remaining == 0:
                self._step_remaining = self._display_substeps
            elif kind == "cancel":
                self._cancelled = True


@dataclass(frozen=True)
class WorkerContext:
    run_id: str
    configuration: RunConfiguration
    output_directory: Path
    events: PipeEventSink
    controls: ControlGate


@dataclass
class _RunRecord:
    run_id: str
    configuration: RunConfiguration
    output_directory: Path
    state: str = "initializing"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    events: list[dict[str, Any]] = field(default_factory=list)
    event_lines: list[bytes] = field(default_factory=list)
    event_ids: set[str] = field(default_factory=set)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage: DiscoveryStage = DiscoveryStage.OBSERVE
    stage_instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    iteration_id: int = 0
    repository: EventRepository | None = None
    process: mp.Process | None = None
    event_receive: Any = None
    command_send: Any = None
    monitor: threading.Thread | None = None
    terminal_event: threading.Event = field(default_factory=threading.Event)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_entrypoint(path: str) -> Callable[[WorkerContext], Any]:
    module_name, separator, name = path.partition(":")
    if not separator:
        raise ValueError("worker entrypoint must use 'module:name' syntax")
    target = getattr(importlib.import_module(module_name), name)
    if not callable(target):
        raise TypeError(f"worker entrypoint {path!r} is not callable")
    return target


def _set_parent_death_signal() -> None:
    if os.name != "posix" or not hasattr(signal, "SIGTERM"):
        return
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (AttributeError, OSError):
        return


def _worker_bootstrap(
    run_id: str,
    configuration: dict[str, Any],
    output_directory: str,
    event_send: Any,
    command_receive: Any,
    entrypoint: str,
) -> None:
    _set_parent_death_signal()
    sink = PipeEventSink(event_send)
    gate = ControlGate(command_receive, sink)
    context = WorkerContext(
        run_id=run_id,
        configuration=RunConfiguration.from_mapping(configuration),
        output_directory=Path(output_directory),
        events=sink,
        controls=gate,
    )
    try:
        sink.emit({"type": "run.started", "payload": {"state": "running", "worker_pid": os.getpid()}})
        result = _resolve_entrypoint(entrypoint)(context)
        _emit_output_artifacts(context)
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        sink.emit({"type": "run.completed", "payload": {"state": "completed", "result": payload}})
    except RunCancelled as exc:
        sink.emit({"type": "run.cancelled", "payload": {"state": "cancelled", "reason": str(exc)}})
    except BaseException as exc:  # worker failures must cross the process boundary
        sink.emit(
            {
                "type": "run.failed",
                "payload": {
                    "state": "failed",
                    "reason": str(exc),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )
    finally:
        event_send.close()
        command_receive.close()


_EVALUATOR_ARTIFACTS = frozenset(
    {
        "reexecution.json",
        "reexecution_report.json",
        "estimated_vs_true.png",
        "discovery.gif",
        "discovery_timeline.html",
        "scene_replay.bin",
    }
)


def _emit_output_artifacts(context: WorkerContext) -> None:
    """Publish allowlisted top-level run artifacts after the runner flushes them."""
    for path in sorted(context.output_directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        context.events.emit(
            {
                "type": "artifact.created",
                "payload": {
                    "artifact_id": str(uuid.uuid4()),
                    "kind": path.stem,
                    "relative_path": path.name,
                    "media_type": _artifact_media_type(path),
                    "filename": path.name,
                    "classification": ("evaluator" if path.name in _EVALUATOR_ARTIFACTS else "public"),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                },
            }
        )


def _artifact_media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".csv": "text/csv",
        ".html": "text/html",
        ".png": "image/png",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".bin": "application/octet-stream",
    }.get(path.suffix.lower(), "application/octet-stream")


def default_newton_worker(context: WorkerContext) -> Any:
    """Lazy adapter for the existing episode runner.

    As the scientific runner acquires ``event_sink`` and ``control_gate``
    parameters, they are passed automatically without coupling this module to
    its implementation version.
    """
    from .runner import run_episode

    kwargs: dict[str, Any] = {
        "seed": context.configuration.seed,
        "output_directory": context.output_directory,
        "device": context.configuration.device,
        "include_robot": context.configuration.include_robot,
    }
    parameters = inspect.signature(run_episode).parameters
    if "event_sink" in parameters:
        kwargs["event_sink"] = context.events
    if "control_gate" in parameters:
        kwargs["control_gate"] = context.controls
    if "material_bounds" in parameters:
        kwargs["material_bounds"] = dict(context.configuration.material_bounds)
    return run_episode(**kwargs)


def fixture_worker(context: WorkerContext) -> dict[str, Any]:
    """Small spawn-safe worker used by CPU integration tests and UI fixtures."""
    steps = int(context.configuration.material_bounds.get("fixture_steps", 2))
    delay = float(context.configuration.material_bounds.get("fixture_delay_s", 0.01))
    if context.configuration.material_bounds.get("fixture_fail"):
        raise RuntimeError("requested fixture failure")
    if context.configuration.material_bounds.get("fixture_crash"):
        os._exit(17)
    if context.configuration.material_bounds.get("fixture_artifacts"):
        public_path = context.output_directory / "public.txt"
        public_path.write_text("public fixture\n")
        context.events.emit(
            {
                "type": "artifact.created",
                "payload": {
                    "artifact_id": "a9a83db5-aadb-4f96-8074-b76301e29f6e",
                    "kind": "fixture_log",
                    "relative_path": "public.txt",
                    "media_type": "text/plain",
                    "filename": "public.txt",
                    "classification": "public",
                    "bytes": public_path.stat().st_size,
                    "sha256": "a" * 64,
                },
            }
        )
        evaluator_path = context.output_directory / "evaluator.txt"
        evaluator_path.write_text("evaluator fixture\n")
        context.events.emit(
            {
                "type": "artifact.created",
                "payload": {
                    "artifact_id": "584788d7-3546-43a6-b2d7-82fd9b6285ad",
                    "kind": "fixture_evaluator_log",
                    "relative_path": "evaluator.txt",
                    "media_type": "text/plain",
                    "filename": "evaluator.txt",
                    "classification": "evaluator",
                    "bytes": evaluator_path.stat().st_size,
                    "sha256": "b" * 64,
                },
            }
        )
    for frame_id in range(steps):
        for _ in range(8):
            context.controls.before_step()
            time.sleep(delay / 8)
        context.events.emit(
            {
                "type": "simulation.visual_frame",
                "frame_id": frame_id,
                "sim_time_s": (frame_id + 1) / 30,
                "payload": {
                    "frame_id": frame_id,
                    "source_state_sequence": frame_id,
                    "scene": "truth",
                    "world_instance_id": "616ae553-abcc-46e1-bb3a-d7d52414c1db",
                    "scenario_id": "fixture",
                    "body_manifest_id": "fixture-v1",
                    "sim_time_s": (frame_id + 1) / 30,
                    "presentation_time_s": (frame_id + 1) / 30,
                    "bodies": [],
                },
            }
        )
    return {"fixture": True, "steps": steps}


class RunManager:
    """Own one active spawned worker and the ordered public run state."""

    def __init__(
        self,
        artifact_root: str | Path,
        *,
        repository: EventRepository | None = None,
        worker_entrypoint: str = "curious_robot.run_manager:default_newton_worker",
        mp_context: mp.context.BaseContext | None = None,
        cancel_grace_s: float = 5.0,
        terminate_grace_s: float = 5.0,
    ) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._repository = repository
        self._worker_entrypoint = worker_entrypoint
        self._context = mp_context or mp.get_context("spawn")
        self._cancel_grace_s = cancel_grace_s
        self._terminate_grace_s = terminate_grace_s
        self._runs: dict[str, _RunRecord] = {}
        self._active_run_id: str | None = None
        self._condition = threading.Condition(threading.RLock())
        self._closed = False

    @property
    def active_run_id(self) -> str | None:
        with self._condition:
            return self._active_run_id

    def start_run(self, configuration: RunConfiguration | Mapping[str, Any]) -> dict[str, Any]:
        config = (
            configuration
            if isinstance(configuration, RunConfiguration)
            else RunConfiguration.from_mapping(configuration)
        )
        validate_public_data(config.public_dict())
        with self._condition:
            if self._closed:
                raise RunManagerError("run manager is closed")
            if self._active_run_id is not None:
                active = self._runs[self._active_run_id]
                if active.state not in TERMINAL_STATES:
                    raise ActiveRunError(active.run_id)
            run_id = str(uuid.uuid4())
            output = (self.artifact_root / run_id).resolve()
            output.mkdir(parents=True, exist_ok=False)
            record = _RunRecord(run_id=run_id, configuration=config, output_directory=output)
            self._runs[run_id] = record
            self._active_run_id = run_id

            event_receive, event_send = self._context.Pipe(duplex=False)
            command_receive, command_send = self._context.Pipe(duplex=False)
            process = self._context.Process(
                target=_worker_bootstrap,
                args=(
                    run_id,
                    config.public_dict() | {"evaluator_mode": config.evaluator_mode},
                    str(output),
                    event_send,
                    command_receive,
                    self._worker_entrypoint,
                ),
                name=f"curious-robot-{run_id[:8]}",
                daemon=False,
            )
            record.process = process
            record.event_receive = event_receive
            record.command_send = command_send
            process.start()
            event_send.close()
            command_receive.close()
            record.monitor = threading.Thread(
                target=self._monitor_worker,
                args=(record,),
                name=f"run-monitor-{run_id[:8]}",
                daemon=True,
            )
            record.monitor.start()
            return self._snapshot_locked(record)

    def reset_run(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
            configuration = record.configuration
        return self.start_run(configuration)

    def pause(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
            if record.state in {"pause_pending", "paused"}:
                return {"run_id": run_id, "state": record.state}
            if record.state != "running":
                raise InvalidRunStateError(f"cannot pause run in {record.state}")
            record.state = "pause_pending"
            self._send_command(record, "pause")
            return {"run_id": run_id, "state": "pause_pending"}

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
            if record.state in {"resume_pending", "running"}:
                return {"run_id": run_id, "state": record.state}
            if record.state != "paused":
                raise InvalidRunStateError(f"cannot resume run in {record.state}")
            record.state = "resume_pending"
            self._send_command(record, "resume")
            return {"run_id": run_id, "state": "resume_pending"}

    def step(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
            if record.state != "paused":
                raise InvalidRunStateError(f"cannot step run in {record.state}")
            self._send_command(record, "step")
            return {"run_id": run_id, "state": "paused", "display_substeps": 8}

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
            if record.state in TERMINAL_STATES:
                raise InvalidRunStateError(f"cannot cancel terminal run in {record.state}")
            if record.state == "cancelling":
                return {"run_id": run_id, "state": "cancelling"}
            record.state = "cancelling"
            self._send_command(record, "cancel")
            threading.Thread(target=self._escalate_cancel, args=(record,), daemon=True).start()
            return {"run_id": run_id, "state": "cancelling"}

    def snapshot(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            return self._snapshot_locked(self._record(run_id))

    def list_runs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._condition:
            records = list(self._runs.values())
            if status:
                records = [record for record in records if record.state == status]
            records.sort(key=lambda record: record.created_at, reverse=True)
            return [self._snapshot_locked(record) for record in records]

    def events_after(self, run_id: str, after_sequence: int) -> list[dict[str, Any]]:
        with self._condition:
            record = self._record(run_id)
            latest = len(record.events)
            if after_sequence < 0 or after_sequence > latest:
                raise CursorError(f"cursor {after_sequence} is outside the committed range 0..{latest}")
            return [event.copy() for event in record.events[after_sequence:]]

    def committed_after(self, run_id: str, after_sequence: int) -> list[tuple[dict[str, Any], bytes]]:
        """Return event values paired with their canonical committed JSON bytes."""
        with self._condition:
            record = self._record(run_id)
            latest = len(record.events)
            if after_sequence < 0 or after_sequence > latest:
                raise CursorError("snapshot required")
            return [
                (record.events[index].copy(), record.event_lines[index])
                for index in range(after_sequence, latest)
            ]

    def wait_for_events(
        self, run_id: str, after_sequence: int, timeout_s: float = 15.0
    ) -> tuple[list[dict[str, Any]], bool]:
        with self._condition:
            record = self._record(run_id)
            if after_sequence < 0 or after_sequence > len(record.events):
                raise CursorError("snapshot required")
            if after_sequence == len(record.events) and record.state not in TERMINAL_STATES:
                self._condition.wait_for(
                    lambda: len(record.events) > after_sequence or record.state in TERMINAL_STATES,
                    timeout=timeout_s,
                )
            return (
                [event.copy() for event in record.events[after_sequence:]],
                record.state in TERMINAL_STATES,
            )

    def wait_for_committed(
        self, run_id: str, after_sequence: int, timeout_s: float = 15.0
    ) -> tuple[list[tuple[dict[str, Any], bytes]], bool]:
        """Atomically catch up and then wait for exact committed event bytes."""
        with self._condition:
            record = self._record(run_id)
            if after_sequence < 0 or after_sequence > len(record.events):
                raise CursorError("snapshot required")
            if after_sequence == len(record.events) and record.state not in TERMINAL_STATES:
                self._condition.wait_for(
                    lambda: len(record.events) > after_sequence or record.state in TERMINAL_STATES,
                    timeout=timeout_s,
                )
            return (
                [
                    (record.events[index].copy(), record.event_lines[index])
                    for index in range(after_sequence, len(record.events))
                ],
                record.state in TERMINAL_STATES,
            )

    def ndjson(self, run_id: str) -> bytes:
        with self._condition:
            record = self._record(run_id)
            if record.state not in TERMINAL_STATES:
                raise InvalidRunStateError("events.ndjson is frozen only for terminal runs")
            return b"".join(line + b"\n" for line in record.event_lines)

    def artifact(self, run_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        with self._condition:
            record = self._record(run_id)
            metadata = record.artifacts.get(artifact_id)
            if metadata is None:
                raise RunNotFoundError("artifact not found")
            if metadata.get("classification", "public") == "evaluator" and (
                record.state not in TERMINAL_STATES or not record.configuration.evaluator_mode
            ):
                raise PermissionError("evaluator artifact is not available")
            relative = Path(str(metadata["relative_path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise PermissionError("unsafe artifact path")
            candidate = (record.output_directory / relative).resolve()
            if not candidate.is_relative_to(record.output_directory) or not candidate.is_file():
                raise RunNotFoundError("artifact not found")
            return candidate, metadata.copy()

    def wait(self, run_id: str, timeout_s: float | None = None) -> dict[str, Any]:
        with self._condition:
            record = self._record(run_id)
        if not record.terminal_event.wait(timeout_s):
            raise TimeoutError(f"run {run_id} did not terminate")
        return self.snapshot(run_id)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            active_id = self._active_run_id
        if active_id:
            try:
                record = self._runs[active_id]
                if record.state not in TERMINAL_STATES:
                    self.cancel(active_id)
                    record.terminal_event.wait(self._cancel_grace_s + self._terminate_grace_s + 1)
            except RunManagerError:
                pass

    def _record(self, run_id: str) -> _RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(f"run {run_id} not found") from exc

    def _send_command(self, record: _RunRecord, command: str) -> None:
        try:
            record.command_send.send_bytes(json.dumps({"command": command}).encode())
        except (BrokenPipeError, EOFError, OSError) as exc:
            raise InvalidRunStateError("physics worker is no longer accepting commands") from exc

    def _monitor_worker(self, record: _RunRecord) -> None:
        record.repository = self._repository or EventStore(
            record.output_directory, run_id=uuid.UUID(record.run_id)
        )
        try:
            while True:
                try:
                    raw = record.event_receive.recv_bytes()
                except EOFError:
                    break
                try:
                    intent = json.loads(raw)
                    if not isinstance(intent, dict) or not isinstance(intent.get("type"), str):
                        raise ValueError("worker intent requires a string type")
                    self._commit(record, intent)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._commit(
                        record,
                        {
                            "type": "run.failed",
                            "payload": {
                                "state": "failed",
                                "reason": str(exc),
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            },
                        },
                    )
                    break
                if record.state in TERMINAL_STATES:
                    break
        finally:
            record.process.join(timeout=1)
            with self._condition:
                if record.state not in TERMINAL_STATES:
                    terminal_type = "run.cancelled" if record.state == "cancelling" else "run.failed"
                    terminal_state = terminal_type.removeprefix("run.")
                    try:
                        self._commit_locked(
                            record,
                            {
                                "type": terminal_type,
                                "payload": {
                                    "state": terminal_state,
                                    "reason": "physics worker exited before terminalization",
                                    "error_type": "WorkerExited",
                                    "message": f"physics worker exited with code {record.process.exitcode}",
                                },
                            },
                        )
                    except Exception:
                        # Persistence can itself be the failed subsystem.  Keep
                        # that failure out-of-band because appending another
                        # event to a failed store is unsafe.
                        record.state = "failed"
                        if self._active_run_id == record.run_id:
                            self._active_run_id = None
                        record.terminal_event.set()
                        self._condition.notify_all()
                record.event_receive.close()
                record.command_send.close()
                if self._repository is None and record.repository is not None:
                    record.repository.close()

    def _commit(self, record: _RunRecord, intent: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            return self._commit_locked(record, intent)

    def _commit_locked(self, record: _RunRecord, intent: dict[str, Any]) -> dict[str, Any]:
        if record.state in TERMINAL_STATES:
            return record.events[-1]
        event_id = str(intent.get("event_id") or uuid.uuid4())
        if event_id in record.event_ids:
            raise RunManagerError(f"duplicate worker event_id {event_id}")
        candidate = {
            "event_id": event_id,
            "run_id": record.run_id,
            "sequence": len(record.events) + 1,
            "wall_time": _utc_now(),
            "sim_time_s": intent.get("sim_time_s"),
            "frame_id": intent.get("frame_id"),
            "type": intent["type"],
            "payload": dict(intent.get("payload", {})),
            "schema_version": str(intent.get("schema_version", "1.0.0")),
            "stage": intent.get("stage", record.stage.value),
            "iteration_id": int(intent.get("iteration_id", record.iteration_id)),
            "stage_instance_id": intent.get("stage_instance_id", record.stage_instance_id),
        }
        typed_event = EVENT_ADAPTER.validate_python(candidate)
        if record.repository is None:
            raise RunManagerError("event repository is not initialized")
        committed = record.repository.append(typed_event)
        line = getattr(committed, "sse_data", None)
        if line is None:
            line = json.dumps(
                typed_event.model_dump(mode="json"),
                separators=(",", ":"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        event = json.loads(line)
        record.events.append(event)
        record.event_lines.append(line)
        record.event_ids.add(event_id)
        record.updated_at = event["wall_time"]
        event_type = event["type"]
        if event_type == "run.started":
            record.state = "running"
        elif event_type == "run.paused":
            record.state = "paused"
        elif event_type == "run.resumed":
            record.state = "running"
        elif event_type.startswith("artifact."):
            payload = event["payload"]
            artifact_id = payload.get("artifact_id")
            if artifact_id:
                record.artifacts[str(artifact_id)] = payload.copy()
        elif event_type == "stage.entered":
            record.stage = DiscoveryStage(event["stage"])
            record.stage_instance_id = str(event["stage_instance_id"])
            record.iteration_id = int(event["iteration_id"])
        elif event_type.startswith("run.") and event_type.removeprefix("run.") in TERMINAL_STATES:
            record.state = event_type.removeprefix("run.")
            if self._active_run_id == record.run_id:
                self._active_run_id = None
            record.terminal_event.set()
        self._condition.notify_all()
        return event.copy()

    def _snapshot_locked(self, record: _RunRecord) -> dict[str, Any]:
        latest_frames: dict[str, dict[str, Any]] = {}
        for event in record.events:
            if event["type"] == "simulation.visual_frame":
                payload = event["payload"]
                key = f"{payload.get('world_instance_id', 'truth')}:{payload.get('scenario_id', 'main')}"
                latest_frames[key] = event.copy()
        return {
            "run_id": record.run_id,
            "state": record.state,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "snapshot_sequence": len(record.events),
            "configuration": record.configuration.public_dict(),
            "latest_visual_frames": latest_frames,
            "artifacts": [
                {key: value for key, value in metadata.items() if key != "relative_path"}
                for metadata in record.artifacts.values()
                if metadata.get("classification", "public") == "public"
                or (record.state in TERMINAL_STATES and record.configuration.evaluator_mode)
            ],
        }

    def _escalate_cancel(self, record: _RunRecord) -> None:
        if record.terminal_event.wait(self._cancel_grace_s):
            return
        if record.process.is_alive():
            record.process.terminate()
        if record.terminal_event.wait(self._terminate_grace_s):
            return
        if record.process.is_alive():
            record.process.kill()


def iter_sse(events: Iterator[dict[str, Any]]) -> Iterator[bytes]:
    """Serialize already committed events without modifying their payload."""
    for event in events:
        data = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {data}\n\n".encode()
