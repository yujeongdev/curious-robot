"""Content-addressed storage with explicit agent/evaluator authority roots."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactVisibility,
    JsonValue,
    canonical_json_bytes,
)


class ArtifactAuthorityError(PermissionError):
    """An artifact crossed its declared authority boundary."""


class ArtifactKindPermissionError(ArtifactAuthorityError):
    """A scoped store capability was used for an undeclared artifact kind."""


def _rejected_payload_id(value: JsonValue) -> str:
    """Fingerprint invalid JSON inputs without permitting them into an artifact body."""

    diagnostic_bytes = json.dumps(
        value,
        allow_nan=True,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(b"rejected-json-v1\0" + diagnostic_bytes).hexdigest()
    return f"sha256:{digest}"


class ContentAddressedArtifactStore:
    def __init__(self, root: Path, *, visibility: ArtifactVisibility) -> None:
        self.root = root
        self.visibility = visibility
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, envelope: ArtifactEnvelope) -> ArtifactRef:
        if envelope.visibility is not self.visibility:
            raise ArtifactAuthorityError(
                f"{self.visibility.value} store rejects {envelope.visibility.value} artifact"
            )
        if self.visibility is ArtifactVisibility.AGENT and any(
            item.visibility is ArtifactVisibility.EVALUATOR for item in envelope.provenance
        ):
            raise ArtifactAuthorityError("agent artifacts cannot reference evaluator-only provenance")
        document = envelope.canonical_document()
        payload = canonical_json_bytes(document)
        reference = envelope.ref()
        destination = self._path(reference)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise RuntimeError(f"content collision for {reference.artifact_id}")
            return reference
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def record_validation_failure(
        self,
        *,
        attempted_kind: str,
        rejected_payload: JsonValue,
        errors: tuple[str, ...],
        validation_conditions: tuple[str, ...],
        provenance: tuple[ArtifactRef, ...] = (),
        diagnostics: JsonValue = None,
    ) -> ArtifactRef:
        """Preserve a deterministic rejection record without publishing invalid content."""

        if not errors:
            raise ValueError("validation failure must include at least one error")
        payload: dict[str, JsonValue] = {
            "attempted_kind": attempted_kind,
            "errors": list(errors),
            "rejected_payload_id": _rejected_payload_id(rejected_payload),
        }
        if diagnostics is not None:
            payload["diagnostics"] = diagnostics
        return self.put(
            ArtifactEnvelope(
                kind=f"{attempted_kind}.validation_failure",
                visibility=self.visibility,
                outcome=ArtifactOutcome.FAIL,
                payload=payload,
                provenance=provenance,
                validation_conditions=validation_conditions,
            )
        )

    def get(self, reference: ArtifactRef) -> ArtifactEnvelope:
        if reference.visibility is not self.visibility:
            raise ArtifactAuthorityError(
                f"{self.visibility.value} store cannot read {reference.visibility.value} artifact"
            )
        document = json.loads(self._path(reference).read_bytes())
        envelope = ArtifactEnvelope(
            kind=document["kind"],
            payload=document["payload"],
            visibility=ArtifactVisibility(document["visibility"]),
            outcome=ArtifactOutcome(document["outcome"]),
            provenance=tuple(
                ArtifactRef(
                    artifact_id=item["artifact_id"],
                    kind=item["kind"],
                    visibility=ArtifactVisibility(item["visibility"]),
                    outcome=ArtifactOutcome(item["outcome"]),
                )
                for item in document["provenance"]
            ),
            validation_conditions=tuple(document["validation_conditions"]),
            schema_version=document["schema_version"],
        )
        if envelope.ref() != reference:
            raise RuntimeError(f"artifact integrity check failed for {reference.artifact_id}")
        return envelope

    def _path(self, reference: ArtifactRef) -> Path:
        digest = reference.artifact_id.removeprefix("sha256:")
        return self.root / "objects" / "sha256" / digest[:2] / f"{digest[2:]}.json"


class LaboratoryArtifactStores:
    """Trusted coordinator resolver; never pass this aggregate into agent code."""

    def __init__(self, root: Path) -> None:
        self.agent = ContentAddressedArtifactStore(root / "agent", visibility=ArtifactVisibility.AGENT)
        self.evaluator = ContentAddressedArtifactStore(
            root / "evaluator", visibility=ArtifactVisibility.EVALUATOR
        )

    def get(self, reference: ArtifactRef) -> ArtifactEnvelope:
        store = self.agent if reference.visibility is ArtifactVisibility.AGENT else self.evaluator
        return store.get(reference)


class ScopedArtifactStore:
    """Least-authority read/write view over one visibility-specific CAS."""

    __slots__ = ("_read_kinds", "_store", "_write_kinds")

    def __init__(
        self,
        store: ContentAddressedArtifactStore,
        *,
        read_kinds: frozenset[str],
        write_kinds: frozenset[str],
    ) -> None:
        self._store = store
        self._read_kinds = read_kinds
        self._write_kinds = write_kinds

    def get(self, reference: ArtifactRef) -> ArtifactEnvelope:
        if reference.kind not in self._read_kinds:
            raise ArtifactKindPermissionError(f"artifact kind is not readable: {reference.kind}")
        return self._store.get(reference)

    def put(self, envelope: ArtifactEnvelope) -> ArtifactRef:
        if envelope.kind not in self._write_kinds:
            raise ArtifactKindPermissionError(f"artifact kind is not writable: {envelope.kind}")
        return self._store.put(envelope)

    def record_validation_failure(
        self,
        *,
        attempted_kind: str,
        rejected_payload: JsonValue,
        errors: tuple[str, ...],
        validation_conditions: tuple[str, ...],
        provenance: tuple[ArtifactRef, ...] = (),
        diagnostics: JsonValue = None,
    ) -> ArtifactRef:
        if attempted_kind not in self._write_kinds:
            raise ArtifactKindPermissionError(f"artifact failure kind is not writable: {attempted_kind}")
        return self._store.record_validation_failure(
            attempted_kind=attempted_kind,
            rejected_payload=rejected_payload,
            errors=errors,
            validation_conditions=validation_conditions,
            provenance=provenance,
            diagnostics=diagnostics,
        )


def open_agent_artifact_store(root: Path) -> ContentAddressedArtifactStore:
    """Open the sole CAS capability that may be injected into an agent worker."""

    return ContentAddressedArtifactStore(root / "agent", visibility=ArtifactVisibility.AGENT)


def open_evaluator_artifact_store(root: Path) -> ContentAddressedArtifactStore:
    """Open evaluator storage only inside the evaluator/coordinator authority."""

    return ContentAddressedArtifactStore(root / "evaluator", visibility=ArtifactVisibility.EVALUATOR)


def open_agent_artifact_capability(
    root: Path,
    *,
    read_kinds: frozenset[str],
    write_kinds: frozenset[str],
) -> ScopedArtifactStore:
    return ScopedArtifactStore(
        open_agent_artifact_store(root),
        read_kinds=read_kinds,
        write_kinds=write_kinds,
    )


def open_evaluator_artifact_capability(
    root: Path,
    *,
    read_kinds: frozenset[str],
    write_kinds: frozenset[str],
) -> ScopedArtifactStore:
    return ScopedArtifactStore(
        open_evaluator_artifact_store(root),
        read_kinds=read_kinds,
        write_kinds=write_kinds,
    )
