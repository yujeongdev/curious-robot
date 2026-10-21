"""Immutable contracts for the real-to-sim-to-real laboratory pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Encode JSON deterministically and reject non-portable numeric values."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_id(value: JsonValue) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


class ArtifactVisibility(StrEnum):
    AGENT = "agent"
    EVALUATOR = "evaluator"


class ArtifactOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class PipelineLane(StrEnum):
    REALITY = "reality"
    TWIN = "twin"
    DEPLOYMENT = "deployment"
    CAPABILITY_BUILD = "capability_build"


class LabArtifactKind(StrEnum):
    CAPABILITY_GAP = "capability_gap"
    TRAJECTORY_DATASET = "trajectory_dataset"
    PARAMETER_POSTERIOR = "parameter_posterior"
    WORLD_SPEC_PATCH = "world_spec_patch"
    TWIN_BUILD = "twin_build"
    TWIN_VALIDATION_REPORT = "twin_validation_report"
    POLICY_ARTIFACT = "policy_artifact"
    REALITY_DEPLOYMENT = "reality_deployment"
    EVALUATION_REPORT = "evaluation_report"
    CAPABILITY_ARTIFACT = "capability_artifact"
    CAPABILITY_ATTESTATION = "capability_attestation"


class LabOperation(StrEnum):
    FRICTION_EXPERIMENT = "friction_experiment"
    BUILD_TWIN = "build_twin"
    VALIDATE_POLICY = "validate_policy"
    DEPLOY_POLICY = "deploy_policy"
    EVALUATE_TERMINAL = "evaluate_terminal"
    DEPLOY_CAPABILITY = "deploy_capability"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    visibility: ArtifactVisibility
    outcome: ArtifactOutcome

    def __post_init__(self) -> None:
        object.__setattr__(self, "visibility", ArtifactVisibility(self.visibility))
        object.__setattr__(self, "outcome", ArtifactOutcome(self.outcome))
        if not self.kind:
            raise ValueError("artifact kind must not be empty")
        prefix, separator, digest = self.artifact_id.partition(":")
        if separator != ":" or prefix != "sha256" or len(digest) != 64:
            raise ValueError("artifact_id must be a sha256 content ID")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("artifact_id must contain a lowercase hexadecimal digest")


@dataclass(frozen=True, slots=True, init=False)
class ArtifactEnvelope:
    kind: str
    _payload: bytes = field(repr=False)
    visibility: ArtifactVisibility
    outcome: ArtifactOutcome
    provenance: tuple[ArtifactRef, ...]
    validation_conditions: tuple[str, ...]
    schema_version: str

    def __init__(
        self,
        kind: str,
        payload: JsonValue,
        visibility: ArtifactVisibility = ArtifactVisibility.AGENT,
        outcome: ArtifactOutcome = ArtifactOutcome.PASS,
        provenance: tuple[ArtifactRef, ...] = (),
        validation_conditions: tuple[str, ...] = (),
        schema_version: str = "1.0.0",
    ) -> None:
        if not kind or not schema_version:
            raise ValueError("artifact kind and schema version must not be empty")
        if not validation_conditions or any(
            not isinstance(condition, str) or not condition.strip() for condition in validation_conditions
        ):
            raise ValueError("artifact validation conditions must not be empty")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "_payload", canonical_json_bytes(payload))
        object.__setattr__(self, "visibility", ArtifactVisibility(visibility))
        object.__setattr__(self, "outcome", ArtifactOutcome(outcome))
        object.__setattr__(self, "provenance", tuple(provenance))
        object.__setattr__(
            self,
            "validation_conditions",
            tuple(condition.strip() for condition in validation_conditions),
        )
        object.__setattr__(self, "schema_version", schema_version)

    @property
    def payload(self) -> JsonValue:
        return json.loads(self._payload)

    def canonical_document(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "outcome": self.outcome.value,
            "payload": self.payload,
            "provenance": [
                {
                    "artifact_id": item.artifact_id,
                    "kind": item.kind,
                    "outcome": item.outcome.value,
                    "visibility": item.visibility.value,
                }
                for item in self.provenance
            ],
            "schema_version": self.schema_version,
            "validation_conditions": list(self.validation_conditions),
            "visibility": self.visibility.value,
        }

    @property
    def artifact_id(self) -> str:
        return content_id(self.canonical_document())

    def ref(self) -> ArtifactRef:
        return ArtifactRef(self.artifact_id, self.kind, self.visibility, self.outcome)


@dataclass(frozen=True, slots=True)
class WorldSpec:
    """Task-oriented twin input, stored as canonical JSON rather than mutable dicts."""

    operation_schema: str
    _document: bytes = field(repr=False)
    sources: tuple[ArtifactRef, ...] = ()
    applied_patch_ids: tuple[str, ...] = ()
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.operation_schema or not self.schema_version:
            raise ValueError("operation schema and schema version must not be empty")
        value = json.loads(self._document)
        if not isinstance(value, dict):
            raise TypeError("WorldSpec document must be an object")
        expected_sections = {"task", "public_geometry", "numerical_config", "model_parameters"}
        if set(value) != expected_sections:
            raise ValueError(f"WorldSpec sections must be exactly {sorted(expected_sections)}")
        if any(not isinstance(value[section], dict) for section in expected_sections):
            raise TypeError("every WorldSpec section must be an object")
        object.__setattr__(self, "_document", canonical_json_bytes(value))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "applied_patch_ids", tuple(self.applied_patch_ids))
        if any(
            item.visibility is not ArtifactVisibility.AGENT or item.outcome is not ArtifactOutcome.PASS
            for item in self.sources
        ):
            raise ValueError("WorldSpec sources must be agent-visible PASS artifacts")
        for patch_id in self.applied_patch_ids:
            ArtifactRef(
                patch_id,
                LabArtifactKind.WORLD_SPEC_PATCH.value,
                ArtifactVisibility.AGENT,
                ArtifactOutcome.PASS,
            )

    @classmethod
    def create(
        cls,
        *,
        operation_schema: str,
        document: dict[str, JsonValue],
        sources: tuple[ArtifactRef, ...] = (),
        applied_patch_ids: tuple[str, ...] = (),
        schema_version: str = "1.0.0",
    ) -> WorldSpec:
        return cls(
            operation_schema=operation_schema,
            _document=canonical_json_bytes(document),
            sources=sources,
            applied_patch_ids=applied_patch_ids,
            schema_version=schema_version,
        )

    @property
    def document(self) -> dict[str, JsonValue]:
        value = json.loads(self._document)
        if not isinstance(value, dict):  # pragma: no cover - constructor guarantees this
            raise TypeError("WorldSpec document must be an object")
        return value

    @property
    def spec_id(self) -> str:
        return content_id(
            {
                "document": self.document,
                "operation_schema": self.operation_schema,
                "applied_patch_ids": list(self.applied_patch_ids),
                "schema_version": self.schema_version,
                "sources": [item.artifact_id for item in self.sources],
            }
        )


@dataclass(frozen=True, slots=True, init=False)
class PatchOperation:
    op: Literal["set", "remove"]
    path: tuple[str, ...]
    _value: bytes = field(repr=False)

    def __init__(
        self,
        op: Literal["set", "remove"],
        path: tuple[str, ...],
        value: JsonValue = None,
    ) -> None:
        if op not in ("set", "remove"):
            raise ValueError(f"unsupported patch operation: {op}")
        if not path or any(not part for part in path):
            raise ValueError("patch path must contain non-empty object keys")
        object.__setattr__(self, "op", op)
        object.__setattr__(self, "path", tuple(path))
        object.__setattr__(self, "_value", canonical_json_bytes(value))

    @property
    def value(self) -> JsonValue:
        return json.loads(self._value)


class StaleWorldSpecError(ValueError):
    """A patch was authored against a different WorldSpec revision."""


class WorldSpecEvidenceError(ValueError):
    """A patch cites evidence that is absent, invalid, or not a posterior."""


class ArtifactResolver(Protocol):
    def get(self, reference: ArtifactRef) -> ArtifactEnvelope: ...


@dataclass(frozen=True, slots=True)
class WorldSpecPatch:
    base_spec_id: str
    operations: tuple[PatchOperation, ...]
    evidence: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        ArtifactRef(self.base_spec_id, "world_spec", ArtifactVisibility.AGENT, ArtifactOutcome.PASS)
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not self.operations:
            raise ValueError("WorldSpecPatch must contain at least one operation")
        if any(operation.path[0] != "model_parameters" for operation in self.operations):
            raise ValueError("WorldSpecPatch may change only model_parameters")
        if not self.evidence:
            raise ValueError("WorldSpecPatch requires agent-visible PASS evidence")
        if any(
            item.visibility is not ArtifactVisibility.AGENT
            or item.outcome is not ArtifactOutcome.PASS
            or item.kind != LabArtifactKind.PARAMETER_POSTERIOR.value
            for item in self.evidence
        ):
            raise ValueError("WorldSpecPatch requires agent-visible PASS parameter_posterior evidence")

    @property
    def patch_id(self) -> str:
        return content_id(
            {
                "base_spec_id": self.base_spec_id,
                "evidence": [item.artifact_id for item in self.evidence],
                "operations": [
                    {"op": item.op, "path": list(item.path), "value": item.value} for item in self.operations
                ],
            }
        )

    def apply(self, base: WorldSpec, *, resolver: ArtifactResolver) -> WorldSpec:
        if base.spec_id != self.base_spec_id:
            raise StaleWorldSpecError(
                f"patch base {self.base_spec_id} does not match current spec {base.spec_id}"
            )
        for reference in self.evidence:
            try:
                resolved = resolver.get(reference)
            except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as error:
                raise WorldSpecEvidenceError(
                    f"patch evidence is not present and valid: {reference.artifact_id}"
                ) from error
            if resolved.ref() != reference:
                raise WorldSpecEvidenceError("resolved patch evidence does not match its ref")
        document = base.document
        for operation in self.operations:
            parent: dict[str, Any] = document
            for key in operation.path[:-1]:
                child = parent.get(key)
                if not isinstance(child, dict):
                    raise KeyError(".".join(operation.path))
                parent = child
            leaf = operation.path[-1]
            if operation.op == "remove":
                if leaf not in parent:
                    raise KeyError(".".join(operation.path))
                del parent[leaf]
            else:
                # Round-trip through canonical JSON to prevent caller-owned mutable aliases.
                parent[leaf] = json.loads(canonical_json_bytes(operation.value))
        return WorldSpec.create(
            operation_schema=base.operation_schema,
            document=document,
            sources=tuple(dict.fromkeys((*base.sources, *self.evidence))),
            applied_patch_ids=(*base.applied_patch_ids, self.patch_id),
            schema_version=base.schema_version,
        )


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    capability: str
    required_operations: tuple[str, ...]
    required_evidence_kinds: tuple[str, ...] = ()

    def assess(
        self,
        *,
        available_operations: tuple[str, ...],
        available_evidence_kinds: tuple[str, ...] = (),
    ) -> CapabilityGap:
        operation_set = set(available_operations)
        evidence_set = set(available_evidence_kinds)
        return CapabilityGap(
            requirement=self,
            missing_operations=tuple(
                operation for operation in self.required_operations if operation not in operation_set
            ),
            missing_evidence_kinds=tuple(
                kind for kind in self.required_evidence_kinds if kind not in evidence_set
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    requirement: CapabilityRequirement
    missing_operations: tuple[str, ...]
    missing_evidence_kinds: tuple[str, ...]

    @property
    def is_closed(self) -> bool:
        return not self.missing_operations and not self.missing_evidence_kinds


@dataclass(frozen=True, slots=True, init=False)
class OperationRequest:
    operation: str
    _arguments: bytes = field(repr=False)
    request_id: str

    def __init__(self, operation: str, arguments: JsonValue, request_id: str) -> None:
        if not operation or not request_id:
            raise ValueError("operation and request ID must not be empty")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "_arguments", canonical_json_bytes(arguments))
        object.__setattr__(self, "request_id", request_id)

    @property
    def arguments(self) -> JsonValue:
        return json.loads(self._arguments)


@dataclass(frozen=True, slots=True)
class OperationResult:
    request_id: str
    status: Literal["completed", "rejected", "failed"]
    evidence: tuple[ArtifactRef, ...] = ()
    detail: str | None = None
