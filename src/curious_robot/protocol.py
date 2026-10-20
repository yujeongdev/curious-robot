"""Versioned, persisted robot experiment protocol configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PushProtocol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    payload = protocol.model_dump_json(exclude_none=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
