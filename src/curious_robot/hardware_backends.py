"""Fail-closed discovery and routing for Franka hardware transports."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class HardwareBackendKind(StrEnum):
    PYLIBFRANKA = "pylibfranka"
    ROS2_CONTROL = "ros2_control"


class HardwareBackendUnavailable(RuntimeError):
    """No backend has both dependencies and explicit operational readiness."""


@dataclass(frozen=True, slots=True)
class HardwareBackendContract:
    kind: HardwareBackendKind
    required_modules: tuple[str, ...]
    requires_realtime_host: bool
    state_surface: str
    command_surface: str


@dataclass(frozen=True, slots=True)
class HardwareReadinessEvidence:
    """Operator/runtime evidence; constructing this object never connects to a robot."""

    robot_reachable: bool = False
    external_perception_ready: bool = False
    independent_safety_ready: bool = False
    command_timeout_configured: bool = False
    exclusive_control_confirmed: bool = False
    realtime_host_ready: bool = False
    state_streaming: bool = False
    hardware_component_active: bool = False
    command_controller_active: bool = False


@dataclass(frozen=True, slots=True)
class HardwareBackendProbe:
    contract: HardwareBackendContract
    missing_modules: tuple[str, ...]
    readiness: HardwareReadinessEvidence
    _provenance: object = field(default=None, repr=False, compare=False)

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers = [f"missing module: {module}" for module in self.missing_modules]
        common = {
            "robot_reachable": self.readiness.robot_reachable,
            "external_perception_ready": self.readiness.external_perception_ready,
            "independent_safety_ready": self.readiness.independent_safety_ready,
            "command_timeout_configured": self.readiness.command_timeout_configured,
            "exclusive_control_confirmed": self.readiness.exclusive_control_confirmed,
            "state_streaming": self.readiness.state_streaming,
        }
        blockers.extend(name for name, ready in common.items() if not ready)
        if self.contract.requires_realtime_host and not self.readiness.realtime_host_ready:
            blockers.append("realtime_host_ready")
        if self.contract.kind is HardwareBackendKind.ROS2_CONTROL:
            if not self.readiness.hardware_component_active:
                blockers.append("hardware_component_active")
            if not self.readiness.command_controller_active:
                blockers.append("command_controller_active")
        return tuple(blockers)

    @property
    def ready(self) -> bool:
        return not self.blockers


PYLIBFRANKA_CONTRACT = HardwareBackendContract(
    kind=HardwareBackendKind.PYLIBFRANKA,
    required_modules=("pylibfranka",),
    requires_realtime_host=True,
    command_surface="1 kHz FCI joint/Cartesian position, velocity, or torque control",
    state_surface="Franka RobotState, model, joint state, and estimated external wrench",
)

ROS2_CONTROL_CONTRACT = HardwareBackendContract(
    kind=HardwareBackendKind.ROS2_CONTROL,
    required_modules=("rclpy", "control_msgs", "sensor_msgs", "franka_msgs"),
    requires_realtime_host=True,
    command_surface="franka_hardware ros2_control position/velocity/effort interfaces",
    state_surface="joint state, Franka semantic state, and TCP ForceTorqueSensor interfaces",
)

BACKEND_CONTRACTS = (PYLIBFRANKA_CONTRACT, ROS2_CONTROL_CONTRACT)
CANONICAL_BACKEND_CONTRACTS = {contract.kind: contract for contract in BACKEND_CONTRACTS}
_PROBE_PROVENANCE = object()
DEFAULT_BACKEND_ORDER = (
    HardwareBackendKind.PYLIBFRANKA,
    HardwareBackendKind.ROS2_CONTROL,
)


def probe_hardware_backends(
    readiness: dict[HardwareBackendKind, HardwareReadinessEvidence],
    *,
    module_available: Callable[[str], bool] | None = None,
) -> tuple[HardwareBackendProbe, ...]:
    """Probe imports only; robot/network readiness must be supplied explicitly."""

    module_available = module_available or _module_available
    return tuple(
        HardwareBackendProbe(
            contract=contract,
            missing_modules=tuple(
                module
                for module in contract.required_modules
                if not module_available(module)
            ),
            readiness=readiness.get(contract.kind, HardwareReadinessEvidence()),
            _provenance=_PROBE_PROVENANCE,
        )
        for contract in BACKEND_CONTRACTS
    )


def select_hardware_backend(
    probes: Iterable[HardwareBackendProbe],
    *,
    preference: tuple[HardwareBackendKind, ...] = DEFAULT_BACKEND_ORDER,
) -> HardwareBackendContract:
    """Prefer direct FCI, fall back to ROS 2, and never select partial readiness."""

    by_kind = {probe.contract.kind: probe for probe in probes}
    for kind in preference:
        probe = by_kind.get(kind)
        if probe is not None and is_canonical_probe(probe) and probe.ready:
            return probe.contract
    details = []
    for kind in preference:
        probe = by_kind.get(kind)
        if probe is None:
            details.append(f"{kind.value}: not probed")
        elif not is_canonical_probe(probe):
            details.append(f"{kind.value}: non-canonical contract")
        else:
            details.append(f"{kind.value}: {', '.join(probe.blockers) or 'not ready'}")
    raise HardwareBackendUnavailable("no Franka hardware backend is ready; " + "; ".join(details))


def is_canonical_probe(probe: HardwareBackendProbe) -> bool:
    """Return whether the probe came from this module's dependency probe path."""

    return (
        probe._provenance is _PROBE_PROVENANCE
        and probe.contract == CANONICAL_BACKEND_CONTRACTS.get(probe.contract.kind)
    )


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (AttributeError, ImportError, ModuleNotFoundError, ValueError):
        return False
