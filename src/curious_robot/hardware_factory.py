"""Ordered construction of a fail-closed Franka hardware Reality adapter."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from curious_robot.lab.artifact_store import open_agent_artifact_capability
from curious_robot.lab.contracts import LabArtifactKind

from .hardware_backends import (
    DEFAULT_BACKEND_ORDER,
    HardwareBackendKind,
    HardwareBackendProbe,
    HardwareBackendUnavailable,
    is_canonical_probe,
)
from .hardware_interfaces import FrankaHardwareTransport, HardwarePerception
from .hardware_motion import FrankaPushCalibration
from .hardware_reality import FrankaHardwareReality


def build_franka_hardware_reality(
    *,
    artifact_root: Path,
    probes: Sequence[HardwareBackendProbe],
    transport_factories: Mapping[
        HardwareBackendKind,
        Callable[[], FrankaHardwareTransport],
    ],
    perception: HardwarePerception,
    push_calibration: FrankaPushCalibration,
    command_timeout_s: float = 60.0,
    perception_timeout_s: float = 2.0,
    observation_max_age_s: float = 0.25,
    observation_max_covariance_trace_m2: float = 0.0001,
    expected_perception_source_id: str = "external-perception",
    monotonic: Callable[[], float] = time.monotonic,
) -> FrankaHardwareReality:
    """Select direct FCI first, then ROS 2, and construct the shared adapter."""

    probes_by_kind = {probe.contract.kind: probe for probe in probes}
    failures: list[str] = []
    selected: tuple[HardwareBackendProbe, FrankaHardwareTransport] | None = None
    for kind in DEFAULT_BACKEND_ORDER:
        probe = probes_by_kind.get(kind)
        if probe is None:
            failures.append(f"{kind.value}: not probed")
            continue
        if not is_canonical_probe(probe):
            failures.append(f"{kind.value}: non-canonical contract")
            continue
        if not probe.ready:
            failures.append(f"{kind.value}: {', '.join(probe.blockers)}")
            continue
        if probe.readiness.approved_calibration_id != push_calibration.calibration_id:
            failures.append(f"{kind.value}: calibration is not approved")
            continue
        factory = transport_factories.get(kind)
        if factory is None:
            failures.append(f"{kind.value}: no transport factory")
            continue
        candidate: FrankaHardwareTransport | None = None
        try:
            candidate = factory()
            if candidate.backend_name != kind.value:
                raise ValueError("transport backend identity does not match")
            selected = (probe, candidate)
            break
        except Exception as error:
            if candidate is not None:
                cleanup_errors = _dispose_transport(candidate)
                if cleanup_errors:
                    raise HardwareBackendUnavailable(
                        f"{kind.value} transport cleanup failed; fallback is unsafe: "
                        + ", ".join(cleanup_errors)
                    ) from error
            failures.append(f"{kind.value}: factory failed ({type(error).__name__})")
    if selected is None:
        raise HardwareBackendUnavailable(
            "no usable Franka hardware backend; " + "; ".join(failures)
        )
    probe, transport = selected
    try:
        store = open_agent_artifact_capability(
            artifact_root,
            read_kinds=frozenset(
                {
                    LabArtifactKind.CAPABILITY_ARTIFACT.value,
                    LabArtifactKind.CAPABILITY_ATTESTATION.value,
                    LabArtifactKind.POLICY_ARTIFACT.value,
                    LabArtifactKind.REALITY_DEPLOYMENT.value,
                    LabArtifactKind.TRAJECTORY_DATASET.value,
                }
            ),
            write_kinds=frozenset({LabArtifactKind.REALITY_DEPLOYMENT.value}),
        )
        return FrankaHardwareReality(
            probe=probe,
            transport=transport,
            perception=perception,
            artifact_store=store,
            push_calibration=push_calibration,
            command_timeout_s=command_timeout_s,
            perception_timeout_s=perception_timeout_s,
            observation_max_age_s=observation_max_age_s,
            observation_max_covariance_trace_m2=observation_max_covariance_trace_m2,
            expected_perception_source_id=expected_perception_source_id,
            monotonic=monotonic,
        )
    except Exception as error:
        cleanup_errors = _dispose_transport(transport)
        if cleanup_errors:
            raise HardwareBackendUnavailable(
                "selected transport cleanup failed after adapter construction error: "
                + ", ".join(cleanup_errors)
            ) from error
        raise


def _dispose_transport(transport: FrankaHardwareTransport) -> tuple[str, ...]:
    """Release a rejected transport and report failures that make fallback unsafe."""

    errors: list[str] = []
    try:
        transport.safe_stop()
    except Exception as error:
        errors.append(f"safe_stop_{type(error).__name__}")
    try:
        transport.close()
    except Exception as error:
        errors.append(f"close_{type(error).__name__}")
    return tuple(errors)
