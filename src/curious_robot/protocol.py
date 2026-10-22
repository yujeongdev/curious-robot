"""Versioned, persisted robot experiment protocol configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .trajectory import physics_substeps_for_target_speed


class PushProtocol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    precontact_offset_m: float = Field(gt=0.0)
    safe_height_m: float = Field(gt=0.0)
    approach_speed_m_s: float = Field(gt=0.0)
    contact_speed_m_s: float = Field(gt=0.0)
    push_height_offset_m: float = Field(ge=0.0)
    lateral_ik_compensation_m: float
    command_increment_m: float = Field(gt=0.0)
    physics_substeps_per_command: int = Field(gt=0)
    maximum_attempts: int = Field(gt=0)
    maximum_travel_m: float = Field(gt=0.0)
    maximum_horizontal_force_n: float = Field(gt=0.0)
    safety_stop_fraction: float = Field(gt=0.0, le=1.0)
    force_model_discrepancy_fraction: float = Field(ge=0.0, le=1.0)
    breakaway_displacement_m: float = Field(gt=0.0)
    breakaway_velocity_m_s: float = Field(gt=0.0)
    breakaway_consecutive_frames: int = Field(gt=0)
    minimum_contact_coverage_fraction: float = Field(ge=0.0, le=1.0)
    minimum_valid_contact_samples: int = Field(gt=0)
    maximum_yaw_excursion_rad: float = Field(ge=0.0, le=3.141592653589793)
    # Defaults preserve validation of persisted schema 1.1.0 artifacts. The
    # canonical config writes every value explicitly for new runs.
    minimum_planar_command_m: float = Field(default=1.0e-6, gt=0.0, le=0.01)
    opposing_surface_max_dot: float = Field(default=-0.8, ge=-1.0, lt=0.0)
    post_attempt_settle_s: float = Field(default=0.15, ge=0.0, le=2.0)
    terminal_settle_s: float = Field(default=0.35, ge=0.0, le=2.0)
    contact_surface_id: str = Field(min_length=1)


class PhysicalExperimentProtocol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    static_probe: PushProtocol
    dynamic_probe: PushProtocol
    final_push: PushProtocol


def load_default_protocol() -> PhysicalExperimentProtocol:
    path = Path(__file__).with_name("configs") / "physical_experiments.json"
    return PhysicalExperimentProtocol.model_validate(json.loads(path.read_text()))


def protocol_sha256(protocol: PhysicalExperimentProtocol) -> str:
    """Return the canonical run-protocol fingerprint used by evidence records."""
    # ``exclude_unset`` preserves the canonical bytes of migrated schema 1.1
    # artifacts whose new controller fields were absent. Canonical configs set
    # every field explicitly, so new-run identities remain fully specified.
    payload = protocol.model_dump_json(exclude_none=False, exclude_unset=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def configure_push_protocol(
    protocol: PushProtocol,
    *,
    dt_s: float,
    speed_m_s: float,
    push_height_m: float | None = None,
    lateral_contact_offset_m: float | None = None,
    contact_surface_id: str | None = None,
    safety_stop_fraction: float | None = None,
) -> PushProtocol:
    """Return a fully validated protocol at the requested operating point."""

    payload = protocol.model_dump()
    payload["contact_speed_m_s"] = speed_m_s
    payload["physics_substeps_per_command"] = physics_substeps_for_target_speed(
        increment_m=protocol.command_increment_m,
        speed_m_s=speed_m_s,
        dt_s=dt_s,
    )
    if push_height_m is not None:
        payload["push_height_offset_m"] = push_height_m
    if lateral_contact_offset_m is not None:
        payload["lateral_ik_compensation_m"] = lateral_contact_offset_m
    if contact_surface_id is not None:
        payload["contact_surface_id"] = contact_surface_id
    if safety_stop_fraction is not None:
        payload["safety_stop_fraction"] = safety_stop_fraction
    return PushProtocol.model_validate(payload)
