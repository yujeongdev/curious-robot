"""High-level authority ports shared by simulation and future hardware adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .contracts import ArtifactRef, OperationRequest, OperationResult, WorldSpec


class PortAuthority(StrEnum):
    REALITY = "reality"
    TWIN = "twin"
    EVALUATOR = "evaluator"


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    authority: PortAuthority
    process_id: int
    world_id: str
    model_id: str
    state_id: str
    config_id: str

    @classmethod
    def for_current_process(
        cls,
        *,
        authority: PortAuthority,
        world_id: str,
        model_id: str,
        state_id: str,
        config_id: str,
    ) -> RuntimeIdentity:
        return cls(authority, os.getpid(), world_id, model_id, state_id, config_id)

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", PortAuthority(self.authority))
        if self.process_id <= 0:
            raise ValueError("process_id must be positive")
        identifiers = (self.world_id, self.model_id, self.state_id, self.config_id)
        if any(not identifier for identifier in identifiers):
            raise ValueError("runtime identity fields must not be empty")


def assert_independent_reality_and_twin(
    reality: RuntimeIdentity, twin: RuntimeIdentity
) -> None:
    if reality.authority is not PortAuthority.REALITY:
        raise ValueError("reality identity must carry reality authority")
    if twin.authority is not PortAuthority.TWIN:
        raise ValueError("twin identity must carry twin authority")
    shared = {
        field
        for field in ("process_id", "world_id", "model_id", "state_id", "config_id")
        if getattr(reality, field) == getattr(twin, field)
    }
    if shared:
        raise ValueError(f"Reality and Twin share forbidden runtime identity: {sorted(shared)}")


class RealityPort(Protocol):
    @property
    def evidence_kinds(self) -> frozenset[str]: ...

    @property
    def operations(self) -> frozenset[str]: ...

    @property
    def identity(self) -> RuntimeIdentity: ...

    def execute(self, request: OperationRequest) -> OperationResult: ...

    def capture_evidence(self) -> tuple[ArtifactRef, ...]: ...


class TwinPort(Protocol):
    @property
    def evidence_kinds(self) -> frozenset[str]: ...

    @property
    def operations(self) -> frozenset[str]: ...

    @property
    def identity(self) -> RuntimeIdentity: ...

    def build(self, spec: WorldSpec) -> ArtifactRef: ...

    def execute(self, request: OperationRequest) -> OperationResult: ...


class EvaluatorPort(Protocol):
    @property
    def evidence_kinds(self) -> frozenset[str]: ...

    @property
    def operations(self) -> frozenset[str]: ...

    @property
    def identity(self) -> RuntimeIdentity: ...

    def evaluate_terminal(self, terminal_artifact: ArtifactRef) -> ArtifactRef: ...
