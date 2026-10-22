"""Immutable physical-world configuration and content identities."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .objects import get_object_archetype
from .schemas import PhysicalBelief
from .tasks import get_task_preset

GRAVITY = 9.81
CUBE_EDGE_M = 0.05
TABLE_HEIGHT_M = 0.0
FRANKA_BASE_POSITION_M = (-0.45, 0.0, 0.0)
FRANKA_READY_JOINT_Q = (0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04)
FRANKA_HAND_TO_TCP_M = 0.1034


@dataclass(frozen=True, slots=True)
class PhysicalParameters:
    """Scene-construction parameters. Truth instances stay private to Reality."""

    mass_kg: float
    static_friction: float
    dynamic_friction: float
    com_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    restitution: float = 0.0


ObjectKind = Literal["cube", "t_bar"]

_ARCHETYPE_TO_OBJECT_KIND: dict[str, ObjectKind] = {"cube": "cube", "t-bar": "t_bar"}
_OBJECT_KIND_TO_ARCHETYPE = {value: key for key, value in _ARCHETYPE_TO_OBJECT_KIND.items()}


def object_kind_from_archetype_id(archetype_id: str) -> ObjectKind:
    try:
        return _ARCHETYPE_TO_OBJECT_KIND[archetype_id]
    except KeyError as error:
        raise KeyError(f"unsupported Newton object archetype: {archetype_id}") from error


@dataclass(frozen=True, slots=True)
class RigidBoxPart:
    """One public, object-local box in a compound rigid collision shape."""

    part_id: str
    label: str
    center_m: tuple[float, float, float]
    size_m: tuple[float, float, float]

    @property
    def volume_m3(self) -> float:
        return self.size_m[0] * self.size_m[1] * self.size_m[2]


@dataclass(frozen=True, slots=True)
class ObjectGeometry:
    """Public rigid geometry shared by Reality and learned Twin construction."""

    kind: ObjectKind
    archetype_id: str
    archetype_version: str
    geometry_hash: str
    body_label: str
    parts: tuple[RigidBoxPart, ...]

    @property
    def volume_m3(self) -> float:
        return sum(part.volume_m3 for part in self.parts)

    @property
    def height_m(self) -> float:
        return max(part.center_m[2] + part.size_m[2] / 2.0 for part in self.parts)


@dataclass(frozen=True, slots=True)
class WorldConfig:
    task_id: str = "push-cube-v1"
    device: str = "cuda:0"
    dt_s: float = 1.0 / 240.0
    initial_position_m: tuple[float, float, float] = (-0.12, 0.0, CUBE_EDGE_M / 2)
    initial_yaw_rad: float = 0.0
    target_position_m: tuple[float, float, float] = (0.15, 0.0, CUBE_EDGE_M / 2)
    target_radius_m: float = 0.06
    target_yaw_rad: float = 0.0
    target_yaw_tolerance_rad: float | None = None
    observation_noise_std: float = 0.0002
    force_noise_std: float = 0.015
    include_robot: bool = True
    object_kind: ObjectKind | None = None
    object_archetype_version: str | None = None
    seed: int = 0
    arm_position_stiffness: float = 3000.0
    arm_position_damping: float = 150.0
    contact_arm_position_stiffness: float = 1600.0
    contact_arm_position_damping: float = 100.0
    gripper_position_stiffness: float = 100.0
    gripper_position_damping: float = 10.0
    tool_tip_radius_m: float = 0.012
    tool_tip_density_kg_m3: float = 100.0
    tool_tip_friction: float = 0.5
    maximum_tool_force_n: float = 15.0
    maximum_cartesian_speed_m_s: float = 0.15
    contact_sensor_cutoff_hz: float = 10.0
    prediction_residual_tolerance_m: float = 0.03
    reexecution_yaw_tolerance_rad: float = 0.01
    maximum_joint_target_speed_rad_s: float = 1.5
    ik_position_tolerance_m: float = 0.01
    cartesian_tracking_tolerance_m: float = 0.025
    maximum_cartesian_settle_s: float = 1.0
    maximum_cartesian_corrections: int = 2
    maximum_cartesian_correction_m: float = 0.04

    def __post_init__(self) -> None:
        preset = get_task_preset(self.task_id)
        expected_kind = object_kind_from_archetype_id(preset.object_archetype_id)
        if self.object_kind is None:
            object.__setattr__(self, "object_kind", expected_kind)
        elif self.object_kind != expected_kind:
            raise ValueError(
                f"task {self.task_id} requires object kind {expected_kind}, got {self.object_kind}"
            )
        if self.object_archetype_version is None:
            object.__setattr__(self, "object_archetype_version", preset.object_archetype_version)
        elif self.object_archetype_version != preset.object_archetype_version:
            raise ValueError(
                f"task {self.task_id} requires object version {preset.object_archetype_version}"
            )

    @property
    def config_id(self) -> str:
        """Content-address the complete effective world configuration."""

        canonical = json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))
        return f"world-config:{hashlib.sha256(canonical.encode()).hexdigest()}"


def resolved_object_geometry(config: WorldConfig) -> ObjectGeometry:
    """Resolve public collision geometry without consulting hidden parameters."""

    try:
        archetype_id = _OBJECT_KIND_TO_ARCHETYPE[config.object_kind]  # type: ignore[index]
    except KeyError as error:
        raise ValueError(f"unsupported object kind: {config.object_kind}") from error
    archetype = get_object_archetype(archetype_id, config.object_archetype_version or "")
    return ObjectGeometry(
        kind=config.object_kind,
        archetype_id=archetype.archetype_id,
        archetype_version=archetype.version,
        geometry_hash=archetype.geometry_hash,
        body_label=f"unknown_{config.object_kind}",
        parts=tuple(
            RigidBoxPart(
                part_id=part.part_id,
                label=f"unknown_{config.object_kind}_{part.part_id}_collision"
                if config.object_kind == "t_bar"
                else "unknown_cube_collision",
                center_m=part.center_object_m,
                size_m=tuple(2.0 * value for value in part.half_extents_m),
            )
            for part in archetype.parts
        ),
    )


def compound_box_inertia(
    geometry: ObjectGeometry,
    mass_kg: float,
    com_m: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    """Return inertia about ``com_m`` for volume-weighted compound boxes.

    Geometry is public. Mass and COM are supplied separately so Reality can use
    hidden values while the Twin uses posterior values through the same path.
    """

    if mass_kg <= 0.0:
        raise ValueError("mass_kg must be positive")
    com = np.asarray(com_m, dtype=float)
    if com.shape != (3,) or not np.all(np.isfinite(com)):
        raise ValueError("com_m must contain three finite values")
    inertia = np.zeros((3, 3), dtype=float)
    for part in geometry.parts:
        part_mass = mass_kg * part.volume_m3 / geometry.volume_m3
        sx, sy, sz = part.size_m
        local = np.diag(
            (
                part_mass * (sy * sy + sz * sz) / 12.0,
                part_mass * (sx * sx + sz * sz) / 12.0,
                part_mass * (sx * sx + sy * sy) / 12.0,
            )
        )
        offset = np.asarray(part.center_m, dtype=float) - com
        inertia += local + part_mass * (
            float(np.dot(offset, offset)) * np.eye(3) - np.outer(offset, offset)
        )
    if float(np.linalg.eigvalsh(inertia).min()) <= 0.0:
        raise ValueError("compound inertia must be positive definite")
    return tuple(tuple(float(value) for value in row) for row in inertia)


def physical_belief_snapshot_id(belief: PhysicalBelief) -> str:
    """Content-address the posterior used to construct a belief world."""

    canonical = belief.model_dump_json(exclude_none=False)
    return f"physical-belief:{hashlib.sha256(canonical.encode()).hexdigest()}"
