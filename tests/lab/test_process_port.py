from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from curious_robot.lab.ports import PortAuthority, RuntimeIdentity
from curious_robot.lab.process_port import (
    PortProcessError,
    SpawnedPortProcess,
    send_error,
    send_ready,
    send_result,
)


def _fixture_worker(connection, secret: str) -> None:  # noqa: ANN001
    private_state = {"hidden_truth": secret}
    identity = RuntimeIdentity.for_current_process(
        authority=PortAuthority.REALITY,
        world_id="fixture-world",
        model_id="fixture-model",
        state_id="fixture-state",
        config_id="fixture-config",
    )
    send_ready(connection, identity)
    while True:
        message = connection.recv()
        if message["command"] == "shutdown":
            return
        if message["command"] == "fail":
            send_error(connection, ValueError(private_state["hidden_truth"]))
        else:
            send_result(
                connection,
                {
                    "pid": os.getpid(),
                    "echo": message["payload"],
                    "private_seed_env": os.environ.get("PRIVATE_SEED_FOR_TEST"),
                },
            )


def _startup_crash(_connection) -> None:  # noqa: ANN001
    raise RuntimeError("startup failed")


def _startup_timeout(_connection) -> None:  # noqa: ANN001
    time.sleep(5)


def test_spawned_port_binds_handshake_to_child_pid_and_redacts_errors() -> None:
    secret = "PRIVATE-RUNTIME-SENTINEL-51b7"
    os.environ["PRIVATE_SEED_FOR_TEST"] = secret
    worker = SpawnedPortProcess(
        target=_fixture_worker,
        args=(secret,),
        name="fixture-port",
        timeout_s=10,
        scrub_environment=("PRIVATE_SEED_FOR_TEST",),
    )
    try:
        response = worker.call("echo", {"value": 3})
        assert response == {
            "pid": worker.identity.process_id,
            "echo": {"value": 3},
            "private_seed_env": None,
        }
        with pytest.raises(PortProcessError) as captured:
            worker.call("fail")
        assert secret not in str(captured.value)
        assert "ValueError" in str(captured.value)
    finally:
        worker.close()
        os.environ.pop("PRIVATE_SEED_FOR_TEST", None)


@pytest.mark.parametrize("target", [_startup_crash, _startup_timeout])
def test_startup_failure_reaps_owned_child(target) -> None:  # noqa: ANN001
    before = {child.pid for child in multiprocessing.active_children()}

    with pytest.raises(PortProcessError):
        SpawnedPortProcess(
            target=target,
            args=(),
            name="failing-fixture-port",
            timeout_s=0.1,
        )

    after = {child.pid for child in multiprocessing.active_children()}
    assert after == before


def test_runner_preserves_startup_failure_artifact(tmp_path, monkeypatch) -> None:
    from curious_robot.lab import friction_runner
    from curious_robot.lab.artifact_store import LaboratoryArtifactStores
    from curious_robot.lab.contracts import ArtifactOutcome, ArtifactRef, ArtifactVisibility

    def fail_startup(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise PortProcessError("private startup detail")

    monkeypatch.setattr(friction_runner, "NewtonSimulatedReality", fail_startup)

    with pytest.raises(PortProcessError):
        friction_runner.run_newton_friction_slice(output_directory=tmp_path)

    manifest = json.loads((tmp_path / "friction_capability_manifest.json").read_text())
    reference = ArtifactRef(
        manifest["failure_artifact"],
        "port_startup.validation_failure",
        ArtifactVisibility.AGENT,
        ArtifactOutcome.FAIL,
    )
    failure = LaboratoryArtifactStores(tmp_path).agent.get(reference)
    assert failure.payload["errors"] == ["PortProcessError"]
    assert "private startup detail" not in json.dumps(manifest)


def test_process_start_exception_closes_pipe_endpoints(monkeypatch) -> None:
    before = len(tuple(Path("/proc/self/fd").iterdir()))

    def fail_start(_process) -> None:  # noqa: ANN001
        raise RuntimeError("process start failed")

    monkeypatch.setattr(multiprocessing.process.BaseProcess, "start", fail_start)

    with pytest.raises(RuntimeError, match="process start failed"):
        SpawnedPortProcess(
            target=_fixture_worker,
            args=("secret",),
            name="start-failure-fixture",
            timeout_s=1,
        )

    after = len(tuple(Path("/proc/self/fd").iterdir()))
    assert after == before
