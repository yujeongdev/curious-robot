"""Small spawn-based RPC lifecycle for authority-separated laboratory ports."""

from __future__ import annotations

import multiprocessing
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from multiprocessing.connection import Connection
from typing import Any

from .ports import RuntimeIdentity

_SPAWN_LOCK = threading.Lock()


class PortProcessError(RuntimeError):
    """A port worker crashed, timed out, or rejected its RPC envelope."""


class SpawnedPortProcess:
    """Own one spawned worker and serialize request/response calls over a Pipe."""

    def __init__(
        self,
        *,
        target: Callable[..., None],
        args: tuple[Any, ...],
        name: str,
        timeout_s: float = 900.0,
        scrub_environment: tuple[str, ...] = (),
    ) -> None:
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe()
        self._connection = parent_connection
        self._process = context.Process(
            target=target,
            args=(child_connection, *args),
            name=name,
        )
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._closed = False
        try:
            with _SPAWN_LOCK:
                scrubbed = {key: os.environ.pop(key) for key in scrub_environment if key in os.environ}
                try:
                    self._process.start()
                finally:
                    os.environ.update(scrubbed)
            child_connection.close()
            ready = self._receive()
        except Exception:
            child_connection.close()
            self.close()
            raise
        if ready.get("type") != "ready" or not isinstance(ready.get("identity"), RuntimeIdentity):
            self.close()
            raise PortProcessError(f"{name} did not provide a valid runtime handshake")
        self.identity = ready["identity"]

    def call(self, command: str, payload: Any = None) -> Any:
        if self._closed:
            raise PortProcessError("port worker is closed")
        with self._lock:
            try:
                self._connection.send({"command": command, "payload": payload})
            except (BrokenPipeError, EOFError, OSError) as error:
                raise PortProcessError("port worker IPC send failed") from error
            response = self._receive()
        if response.get("type") == "error":
            raise PortProcessError(f"port worker failed with {response.get('error_type', 'unknown error')}")
        if response.get("type") != "result":
            raise PortProcessError("port worker returned an invalid RPC envelope")
        return response.get("value")

    def _receive(self) -> dict[str, Any]:
        if not self._connection.poll(self._timeout_s):
            raise PortProcessError(f"port worker timed out after {self._timeout_s:.1f}s")
        try:
            response = self._connection.recv()
        except (EOFError, OSError) as error:
            raise PortProcessError(
                f"port worker exited unexpectedly with code {self._process.exitcode}"
            ) from error
        if not isinstance(response, dict):
            raise PortProcessError("port worker returned a non-object RPC envelope")
        return response

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.pid is not None and self._process.is_alive():
            with suppress(BrokenPipeError, EOFError, OSError):
                self._connection.send({"command": "shutdown", "payload": None})
            self._process.join(timeout=5)
        if self._process.pid is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
        if self._process.pid is not None and self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=5)
        self._connection.close()

    def __enter__(self) -> SpawnedPortProcess:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def send_ready(connection: Connection, identity: RuntimeIdentity) -> None:
    connection.send({"type": "ready", "identity": identity})


def send_result(connection: Connection, value: Any) -> None:
    connection.send({"type": "result", "value": value})


def send_error(connection: Connection, error: BaseException) -> None:
    """Report only exception class; messages may contain private worker state."""

    connection.send({"type": "error", "error_type": type(error).__name__})
