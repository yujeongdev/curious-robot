"""Local FastAPI control plane for Agent Lab.

The API deliberately exposes the :class:`RunManager` contract rather than
Newton objects.  Browser mutations require a per-launch token and same-origin
requests; artifacts are resolved by opaque IDs instead of mounting a run
directory.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .events import validate_public_data
from .run_manager import (
    ActiveRunError,
    CursorError,
    InvalidRunStateError,
    RunConfiguration,
    RunManager,
    RunNotFoundError,
)
from .tasks import get_task_preset, list_task_presets, resolve_task

CONTROL_TOKEN_HEADER = "X-Curious-Robot-Control-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default="push-cube-v1", min_length=1, max_length=64)
    seed: int = 7
    device: str = Field(default="cuda:0", min_length=1, max_length=64)
    include_robot: bool = True
    recording: bool = True
    evaluator_mode: bool = False
    material_bounds: dict[str, Any] = Field(default_factory=dict)

    @field_validator("material_bounds")
    @classmethod
    def material_bounds_are_public(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_public_data(value)
        return value

    @field_validator("task_id")
    @classmethod
    def task_is_allowlisted(cls, value: str) -> str:
        try:
            get_task_preset(value)
        except KeyError as error:
            raise ValueError(str(error)) from error
        return value


def create_app(
    *,
    manager: RunManager | None = None,
    artifact_root: str | Path = "artifacts/runs",
    control_token: str | None = None,
    allowed_hosts: frozenset[str] | None = None,
    allow_non_loopback: bool = False,
    static_dir: str | Path | None = None,
) -> FastAPI:
    run_manager = manager or RunManager(artifact_root)
    token = control_token or secrets.token_urlsafe(32)
    hosts = allowed_hosts or frozenset({"127.0.0.1", "localhost", "::1", "testserver"})
    app = FastAPI(title="Curious Robot Agent Lab", version="0.1.1")
    app.state.run_manager = run_manager
    app.state.control_token = token
    web_root = _resolve_static_dir(static_dir)

    @app.middleware("http")
    async def local_security(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.url.hostname
        if host not in hosts and not allow_non_loopback:
            return JSONResponse(status_code=400, content={"detail": "untrusted Host header"})
        if request.method not in SAFE_METHODS:
            supplied = request.headers.get(CONTROL_TOKEN_HEADER)
            if supplied is None or not secrets.compare_digest(supplied, token):
                return JSONResponse(status_code=403, content={"detail": "invalid control token"})
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return JSONResponse(
                    status_code=415,
                    content={"detail": "mutating requests require application/json"},
                )
            origin = request.headers.get("origin")
            if origin is not None and not _same_origin(origin, request):
                return JSONResponse(status_code=403, content={"detail": "cross-origin request rejected"})
        return await call_next(request)

    @app.exception_handler(RunNotFoundError)
    async def not_found(_request: Request, _exc: RunNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "run not found"})

    @app.exception_handler(InvalidRunStateError)
    async def conflict(_request: Request, exc: InvalidRunStateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(CursorError)
    async def cursor_conflict(_request: Request, _exc: CursorError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "event cursor cannot be continued", "snapshot_required": True},
        )

    @app.post("/api/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(body: RunRequest) -> dict[str, Any]:
        try:
            return run_manager.start_run(RunConfiguration.from_mapping(body.model_dump()))
        except ActiveRunError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "another run is active", "active_run_id": exc.run_id},
            ) from exc

    @app.get("/api/tasks")
    async def list_tasks() -> list[dict[str, Any]]:
        return [resolve_task(preset.preset_id).summary() for preset in list_task_presets()]

    @app.get("/api/runs")
    async def list_runs(run_status: str | None = Query(default=None, alias="status")) -> list[dict[str, Any]]:
        return run_manager.list_runs(status=run_status)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return run_manager.snapshot(run_id)

    @app.post("/api/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict[str, Any]:
        return run_manager.pause(run_id)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        return run_manager.resume(run_id)

    @app.post("/api/runs/{run_id}/step")
    async def step_run(run_id: str) -> dict[str, Any]:
        return run_manager.step(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        return run_manager.cancel(run_id)

    @app.post("/api/runs/{run_id}/reset", status_code=status.HTTP_201_CREATED)
    async def reset_run(run_id: str) -> dict[str, Any]:
        try:
            return run_manager.reset_run(run_id)
        except ActiveRunError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "another run is active", "active_run_id": exc.run_id},
            ) from exc

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        after_sequence: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        cursor = _resolve_cursor(after_sequence, last_event_id)
        # Validate synchronously so a bad cursor is an HTTP 409 rather than a
        # late exception after streaming headers have already been sent.
        run_manager.events_after(run_id, cursor)

        async def generate() -> AsyncIterator[bytes]:
            next_sequence = cursor
            while True:
                events, terminal = await asyncio.to_thread(
                    run_manager.wait_for_committed, run_id, next_sequence, 15.0
                )
                if not events:
                    if terminal:
                        break
                    yield b": keep-alive\n\n"
                    continue
                for event, committed_json in events:
                    yield (
                        (f"id: {event['sequence']}\nevent: {event['type']}\ndata: ").encode()
                        + committed_json
                        + b"\n\n"
                    )
                    next_sequence = int(event["sequence"])
                if terminal:
                    break

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs/{run_id}/events.ndjson")
    async def download_events(run_id: str) -> Response:
        return Response(
            content=run_manager.ndjson(run_id),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{run_id}-events.ndjson"'},
        )

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    async def get_artifact(run_id: str, artifact_id: str) -> FileResponse:
        try:
            path, metadata = run_manager.artifact(run_id, artifact_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="artifact is not available") from exc
        return FileResponse(
            path,
            media_type=str(metadata.get("media_type", "application/octet-stream")),
            filename=str(metadata.get("filename", path.name)),
        )

    if web_root is not None:
        assets = web_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="agent-lab-assets")
        models = web_root / "models"
        if models.is_dir():
            app.mount("/models", StaticFiles(directory=models), name="agent-lab-models")

    @app.get("/{spa_path:path}", response_class=HTMLResponse)
    async def agent_lab_shell(spa_path: str) -> HTMLResponse:
        if spa_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        if web_root is None or not (web_root / "index.html").is_file():
            return HTMLResponse(
                status_code=503,
                content=(
                    "<h1>Agent Lab frontend is not built</h1>"
                    "<pre>corepack pnpm --dir src/web install --frozen-lockfile\n"
                    "corepack pnpm --dir src/web build</pre>"
                ),
            )
        html = (web_root / "index.html").read_text()
        html = html.replace(
            '<meta name="curious-robot-control-token" content="" />',
            f'<meta name="curious-robot-control-token" content="{token}" />',
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return app


def _resolve_static_dir(value: str | Path | None) -> Path | None:
    if value is not None:
        candidate = Path(value).resolve()
        return candidate if candidate.is_dir() else None
    packaged = Path(__file__).with_name("web_static")
    if packaged.is_dir():
        return packaged
    repository_build = Path(__file__).resolve().parents[2] / "src" / "web" / "dist"
    return repository_build if repository_build.is_dir() else None


def _same_origin(origin: str, request: Request) -> bool:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return (
        parsed.scheme == request.url.scheme
        and parsed.hostname == request.url.hostname
        and origin_port == request_port
    )


def _resolve_cursor(after_sequence: int | None, last_event_id: str | None) -> int:
    header_cursor: int | None = None
    if last_event_id is not None:
        try:
            header_cursor = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc
        if header_cursor < 0:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be non-negative")
    # Native EventSource reconnects to its original URL while advancing the
    # Last-Event-ID header.  The header therefore supersedes the bootstrap
    # query cursor after the first connection.
    return header_cursor if header_cursor is not None else (after_sequence or 0)
