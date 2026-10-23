"""Command results and diagnostics for humans, agents and native GPU workers."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any
from uuid import uuid4


def output_directory(output: Path | None, operation: str) -> Path:
    return (output or Path("artifacts/curo") / operation / str(uuid4())).resolve()


@contextmanager
def diagnostics_to_stderr() -> Iterator[None]:
    """Redirect Python, native runtime and spawned-worker stdout while working."""
    sys.stdout.flush()
    _flush_native_streams()
    try:
        saved_stdout = os.dup(1)
    except OSError:
        with redirect_stdout(sys.stderr):
            yield
        return
    try:
        os.dup2(2, 1)
        with redirect_stdout(sys.stderr):
            yield
    finally:
        try:
            sys.stderr.flush()
            _flush_native_streams()
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)


def _flush_native_streams() -> None:
    # FD changes do not move libc's buffered printf output. Curo supports POSIX.
    if os.name == "posix":
        runtime = ctypes.CDLL(None)
        runtime.fflush.argtypes = [ctypes.c_void_p]
        runtime.fflush.restype = ctypes.c_int
        runtime.fflush(None)


def emit(document: dict[str, Any], *, as_json: bool = True) -> None:
    if not as_json and document.get("status") == "not_implemented":
        print(f"{document['command']}: {document['message']}")
        print(document["hint"])
    elif not as_json and "commands" in document:
        for command in document["commands"]:
            print(f"{command['command']}\t{command['status']}\t{command['summary']}")
    elif not as_json and "tasks" in document:
        for task in document["tasks"]:
            print(f"{task['task_id']}\t{task['object_label']}\t{task['objective']}")
    elif not as_json and "task_id" in document and "preset" in document:
        print(f"{document['task_id']}@{document['task_version']}: {document['objective']}")
        print(f"Object: {document['object_label']}")
        print(f"Preset: {document['task_preset_hash']}")
    else:
        print(json.dumps(document, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False))


def not_implemented(command: str, dependency_hint: str) -> dict[str, Any]:
    return {
        "schema_version": "curo-command-v1",
        "command": command,
        "status": "not_implemented",
        "message": "아직 구현되지 않았습니다",
        "error": {"code": "not_implemented"},
        "hint": dependency_hint
        + " 지원 상태는 curo capabilities --json으로 확인하세요. 작업을 수행하지 않았습니다.",
    }
