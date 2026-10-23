from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curious_robot.run_manager import RunManager
from curious_robot.web_api import CONTROL_TOKEN_HEADER, create_app

TOKEN = "test-control-token"
FIXTURE_WORKER = "curious_robot.run_manager:fixture_worker"
PUBLIC_ARTIFACT_ID = "a9a83db5-aadb-4f96-8074-b76301e29f6e"
EVALUATOR_ARTIFACT_ID = "584788d7-3546-43a6-b2d7-82fd9b6285ad"


@pytest.fixture
def api(tmp_path: Path):
    manager = RunManager(
        tmp_path,
        worker_entrypoint=FIXTURE_WORKER,
        cancel_grace_s=0.5,
        terminate_grace_s=0.5,
    )
    app = create_app(manager=manager, control_token=TOKEN)
    with TestClient(app) as client:
        yield client, manager
    manager.close()


def create_run(client: TestClient, **material_bounds) -> str:
    response = client.post(
        "/api/runs",
        headers={CONTROL_TOKEN_HEADER: TOKEN},
        json={"material_bounds": material_bounds},
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_task_catalog_api_lists_allowlisted_presets(api) -> None:
    from curious_robot.tasks import get_task_preset

    client, _ = api

    response = client.get("/api/tasks")

    assert response.status_code == 200
    tasks = {item["task_id"]: item for item in response.json()}
    assert set(tasks) == {"push-cube-v1", "push-t-bar-v1"}
    assert tasks["push-cube-v1"]["requires_yaw"] is False
    assert tasks["push-t-bar-v1"]["requires_yaw"] is True
    assert all(item["candidate_count"] >= 1 for item in tasks.values())
    for task_id, summary in tasks.items():
        assert summary["default_material_bounds"] == {
            "mass_kg": list(get_task_preset(task_id).default_mass_range_kg)
        }


def test_mutations_require_token_json_and_same_origin(api) -> None:
    client, _ = api
    assert client.post("/api/runs", json={}).status_code == 403
    assert (
        client.post(
            "/api/runs",
            headers={CONTROL_TOKEN_HEADER: TOKEN, "content-type": "text/plain"},
            content="{}",
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/runs",
            headers={CONTROL_TOKEN_HEADER: TOKEN, "origin": "https://evil.example"},
            json={},
        ).status_code
        == 403
    )
    assert client.get("/api/runs", headers={"host": "evil.example"}).status_code == 400
    forbidden = client.post(
        "/api/runs",
        headers={CONTROL_TOKEN_HEADER: TOKEN},
        json={"material_bounds": {"hidden_mass": [0.1, 0.4]}},
    )
    assert forbidden.status_code == 422
    unsupported_task = client.post(
        "/api/runs",
        headers={CONTROL_TOKEN_HEADER: TOKEN},
        json={"task_id": "unregistered-task"},
    )
    assert unsupported_task.status_code == 422


def test_only_one_active_run_and_terminal_snapshot(api) -> None:
    client, manager = api
    run_id = create_run(client, fixture_steps=100, fixture_delay_s=0.02)
    collision = client.post("/api/runs", headers={CONTROL_TOKEN_HEADER: TOKEN}, json={})
    assert collision.status_code == 409
    assert collision.json()["detail"]["active_run_id"] == run_id

    cancelled = client.post(
        f"/api/runs/{run_id}/cancel",
        headers={CONTROL_TOKEN_HEADER: TOKEN},
        json={},
    )
    assert cancelled.status_code == 200
    manager.wait(run_id, 8)
    snapshot = client.get(f"/api/runs/{run_id}")
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "cancelled"


def test_sse_reconnect_uses_committed_sequence_cursor(api) -> None:
    client, manager = api
    run_id = create_run(client, fixture_steps=3, fixture_delay_s=0.001)
    manager.wait(run_id, 8)
    committed = manager.events_after(run_id, 0)

    response = client.get(
        f"/api/runs/{run_id}/events",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" not in response.text
    for event in committed[1:]:
        assert f"id: {event['sequence']}\n" in response.text
        assert f"event: {event['type']}\n" in response.text
    future = client.get(f"/api/runs/{run_id}/events?after_sequence=9999")
    assert future.status_code == 409
    assert future.json()["snapshot_required"] is True
    reconnected = client.get(
        f"/api/runs/{run_id}/events?after_sequence=1",
        headers={"Last-Event-ID": "2"},
    )
    assert reconnected.status_code == 200
    assert "id: 3" in reconnected.text


def test_ndjson_is_terminal_only(api) -> None:
    client, manager = api
    run_id = create_run(client, fixture_steps=100, fixture_delay_s=0.02)
    active = client.get(f"/api/runs/{run_id}/events.ndjson")
    assert active.status_code == 409

    client.post(
        f"/api/runs/{run_id}/cancel",
        headers={CONTROL_TOKEN_HEADER: TOKEN},
        json={},
    )
    manager.wait(run_id, 8)
    terminal = client.get(f"/api/runs/{run_id}/events.ndjson")
    assert terminal.status_code == 200
    lines = terminal.content.splitlines()
    assert lines
    assert b'"type":"run.cancelled"' in lines[-1]


def test_artifact_route_enforces_registration_and_evaluator_policy(api) -> None:
    client, manager = api
    run_id = create_run(client, fixture_artifacts=True)
    manager.wait(run_id, 8)

    public = client.get(f"/api/runs/{run_id}/artifacts/{PUBLIC_ARTIFACT_ID}")
    assert public.status_code == 200
    assert public.text == "public fixture\n"
    assert client.get(f"/api/runs/{run_id}/artifacts/{EVALUATOR_ARTIFACT_ID}").status_code == 403
    assert client.get(f"/api/runs/{run_id}/artifacts/not-registered").status_code == 404


def test_get_unknown_run_does_not_leak_paths(api) -> None:
    client, _ = api
    response = client.get("/api/runs/not-a-run")
    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_agent_lab_shell_injects_control_token_and_serves_assets(tmp_path: Path) -> None:
    static = tmp_path / "web"
    assets = static / "assets"
    assets.mkdir(parents=True)
    (static / "index.html").write_text(
        '<meta name="curious-robot-control-token" content="" /><script src="/assets/app.js"></script>'
    )
    (assets / "app.js").write_text("console.log('agent lab')")
    manager = RunManager(tmp_path / "runs", worker_entrypoint=FIXTURE_WORKER)
    app = create_app(manager=manager, control_token=TOKEN, static_dir=static)
    try:
        with TestClient(app) as client:
            root = client.get("/")
            assert root.status_code == 200
            assert f'content="{TOKEN}"' in root.text
            assert root.headers["cache-control"] == "no-store"
            nested = client.get("/runs/new")
            assert nested.status_code == 200
            assert client.get("/assets/app.js").status_code == 200
    finally:
        manager.close()


def test_packaged_agent_lab_shell_is_self_contained(tmp_path: Path) -> None:
    manager = RunManager(tmp_path / "runs", worker_entrypoint=FIXTURE_WORKER)
    app = create_app(manager=manager, control_token=TOKEN)
    try:
        with TestClient(app) as client:
            root = client.get("/")
            assert root.status_code == 200
            assert f'content="{TOKEN}"' in root.text
            asset_match = re.search(r'src="(/assets/[^"]+\.js)"', root.text)
            assert asset_match is not None
            assert client.get(asset_match.group(1)).status_code == 200
            model = client.get("/models/franka/fr3.glb")
            assert model.status_code == 200
            assert model.headers["content-type"] == "model/gltf-binary"
    finally:
        manager.close()
