"""Immutable physical-world configuration and content identities."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass

from .schemas import PhysicalBelief

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


@dataclass(frozen=True, slots=True)
class WorldConfig:
    device: str = "cuda:0"
    dt_s: float = 1.0 / 240.0
    initial_position_m: tuple[float, float, float] = (-0.12, 0.0, CUBE_EDGE_M / 2)
    target_position_m: tuple[float, float, float] = (0.15, 0.0, CUBE_EDGE_M / 2)
    target_radius_m: float = 0.06
    observation_noise_std: float = 0.0002
    force_noise_std: float = 0.015
    include_robot: bool = True
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
    maximum_joint_target_speed_rad_s: float = 1.5
    ik_position_tolerance_m: float = 0.01
    cartesian_tracking_tolerance_m: float = 0.025
    maximum_cartesian_settle_s: float = 1.0
    maximum_cartesian_corrections: int = 2
    maximum_cartesian_correction_m: float = 0.04

    @property
    def config_id(self) -> str:
        """Content-address the complete effective world configuration."""

        canonical = json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))
        return f"world-config:{hashlib.sha256(canonical.encode()).hexdigest()}"


def physical_belief_snapshot_id(belief: PhysicalBelief) -> str:
    """Content-address the posterior used to construct a belief world."""

    canonical = belief.model_dump_json(exclude_none=False)
    return f"physical-belief:{hashlib.sha256(canonical.encode()).hexdigest()}"
