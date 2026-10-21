from __future__ import annotations

import json
import multiprocessing
from dataclasses import asdict

import pytest

from curious_robot.lab.ports import (
    PortAuthority,
    RuntimeIdentity,
    assert_independent_reality_and_twin,
)


def _send_runtime_handshake(connection, authority: PortAuthority, prefix: str, secret: str) -> None:  # noqa: ANN001
    private_runtime_state = {"hidden_truth": secret}
    identity = RuntimeIdentity.for_current_process(
        authority=authority,
        world_id=f"{prefix}-world",
        model_id=f"{prefix}-model",
        state_id=f"{prefix}-state",
        config_id=f"{prefix}-config",
    )
    assert private_runtime_state["hidden_truth"] == secret
    connection.send(identity)
    connection.close()


def _identity(authority: PortAuthority, prefix: str, process_id: int) -> RuntimeIdentity:
    return RuntimeIdentity(
        authority=authority,
        process_id=process_id,
        world_id=f"{prefix}-world",
        model_id=f"{prefix}-model",
        state_id=f"{prefix}-state",
        config_id=f"{prefix}-config",
    )


def test_reality_and_twin_runtime_identity_is_fully_independent() -> None:
    reality = _identity(PortAuthority.REALITY, "reality", 101)
    twin = _identity(PortAuthority.TWIN, "twin", 202)

    assert_independent_reality_and_twin(reality, twin)


def test_spawned_handshakes_are_disjoint_and_do_not_leak_private_state() -> None:
    context = multiprocessing.get_context("spawn")
    reality_receiver, reality_sender = context.Pipe(duplex=False)
    twin_receiver, twin_sender = context.Pipe(duplex=False)
    sentinel = "PRIVATE-TRUTH-SENTINEL-93f12"
    reality_process = context.Process(
        target=_send_runtime_handshake,
        args=(reality_sender, PortAuthority.REALITY, "reality", sentinel),
    )
    twin_process = context.Process(
        target=_send_runtime_handshake,
        args=(twin_sender, PortAuthority.TWIN, "twin", "PRIVATE-TWIN-STATE"),
    )
    reality_process.start()
    twin_process.start()
    reality_sender.close()
    twin_sender.close()
    reality = reality_receiver.recv()
    twin = twin_receiver.recv()
    reality_receiver.close()
    twin_receiver.close()
    reality_process.join(timeout=10)
    twin_process.join(timeout=10)

    assert reality_process.exitcode == 0
    assert twin_process.exitcode == 0
    assert reality.process_id == reality_process.pid
    assert twin.process_id == twin_process.pid
    assert_independent_reality_and_twin(reality, twin)
    public_handshake = json.dumps([asdict(reality), asdict(twin)])
    assert sentinel not in public_handshake


@pytest.mark.parametrize("shared_field", ["process_id", "world_id", "model_id", "state_id", "config_id"])
def test_shared_runtime_identity_is_rejected(shared_field: str) -> None:
    reality = _identity(PortAuthority.REALITY, "reality", 101)
    twin_values = {
        "authority": PortAuthority.TWIN,
        "process_id": 202,
        "world_id": "twin-world",
        "model_id": "twin-model",
        "state_id": "twin-state",
        "config_id": "twin-config",
    }
    twin_values[shared_field] = getattr(reality, shared_field)
    twin = RuntimeIdentity(**twin_values)

    with pytest.raises(ValueError, match=shared_field):
        assert_independent_reality_and_twin(reality, twin)
