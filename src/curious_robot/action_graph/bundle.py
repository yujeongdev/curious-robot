"""Content-addressed field bundle and fail-closed runtime admission."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ActionGraph

SHA256_ID = r"^sha256:[0-9a-f]{64}$"


class _BundleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class DeploymentSafetyEnvelope(_BundleModel):
    maximum_force_n: float = Field(gt=0.0)
    maximum_speed_m_s: float = Field(gt=0.0)
    execution_deadline_s: float = Field(gt=0.0)


class DeploymentBundle(_BundleModel):
    schema_version: Literal["deployment-bundle-v1"] = "deployment-bundle-v1"
    bundle_name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    task_id: str = Field(min_length=1)
    task_version: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    object_archetype_version: str = Field(min_length=1)
    geometry_hash: str = Field(pattern=SHA256_ID)
    protocol_binding_id: str = Field(pattern=SHA256_ID)
    calibration_id: str = Field(pattern=SHA256_ID)
    executor_abi: str = Field(min_length=1)
    required_backend_capabilities: tuple[str, ...] = Field(min_length=1)
    validation_artifact_ids: tuple[str, ...] = Field(min_length=1)
    safety: DeploymentSafetyEnvelope
    action_graph: ActionGraph

    @model_validator(mode="after")
    def validate_bindings(self) -> DeploymentBundle:
        if (
            self.action_graph.object_id != self.object_id
            or self.action_graph.protocol_binding_id != self.protocol_binding_id
            or self.action_graph.calibration_id != self.calibration_id
        ):
            raise ValueError("deployment bundle identity does not match its action graph")
        if len(set(self.required_backend_capabilities)) != len(
            self.required_backend_capabilities
        ):
            raise ValueError("deployment bundle capabilities must be unique")
        if len(set(self.validation_artifact_ids)) != len(self.validation_artifact_ids) or any(
            not _is_sha256_id(artifact_id) for artifact_id in self.validation_artifact_ids
        ):
            raise ValueError("deployment validation artifacts must be unique SHA-256 IDs")
        if self.action_graph.wall_clock_deadline_s > self.safety.execution_deadline_s:
            raise ValueError("deployment graph exceeds the bundle execution deadline")
        return self

    @property
    def bundle_id(self) -> str:
        canonical = self.model_dump_json(exclude_none=False)
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


class RuntimeCapabilityManifest(_BundleModel):
    backend_id: str = Field(min_length=1)
    executor_abi: str = Field(min_length=1)
    supported_graph_schema_versions: tuple[str, ...] = Field(min_length=1)
    capabilities: tuple[str, ...] = Field(min_length=1)
    approved_calibration_ids: tuple[str, ...] = Field(min_length=1)
    maximum_force_n: float = Field(gt=0.0)
    maximum_speed_m_s: float = Field(gt=0.0)
    realtime_ready: bool
    watchdog_ready: bool


class BundleAdmission(_BundleModel):
    bundle_id: str = Field(pattern=SHA256_ID)
    backend_id: str
    blockers: tuple[str, ...]

    @property
    def admitted(self) -> bool:
        return not self.blockers


def admit_deployment_bundle(
    bundle: DeploymentBundle,
    runtime: RuntimeCapabilityManifest,
) -> BundleAdmission:
    blockers: list[str] = []
    if runtime.executor_abi != bundle.executor_abi:
        blockers.append("executor_abi")
    if bundle.action_graph.graph_schema_version not in runtime.supported_graph_schema_versions:
        blockers.append("graph_schema_version")
    missing = sorted(set(bundle.required_backend_capabilities) - set(runtime.capabilities))
    blockers.extend(f"missing_capability:{capability}" for capability in missing)
    if bundle.calibration_id not in runtime.approved_calibration_ids:
        blockers.append("approved_calibration")
    if bundle.safety.maximum_force_n > runtime.maximum_force_n:
        blockers.append("maximum_force_n")
    if bundle.safety.maximum_speed_m_s > runtime.maximum_speed_m_s:
        blockers.append("maximum_speed_m_s")
    if not runtime.realtime_ready:
        blockers.append("realtime_ready")
    if not runtime.watchdog_ready:
        blockers.append("watchdog_ready")
    return BundleAdmission(
        bundle_id=bundle.bundle_id,
        backend_id=runtime.backend_id,
        blockers=tuple(blockers),
    )


def _is_sha256_id(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )
