"""Spawned Newton implementations of the laboratory authority ports."""

from __future__ import annotations

import os
from pathlib import Path

from .contracts import (
    ArtifactRef,
    LabArtifactKind,
    LabOperation,
    OperationRequest,
    OperationResult,
    WorldSpec,
)
from .newton_evaluator_worker import _evaluator_worker
from .newton_reality_worker import _reality_worker
from .newton_twin_worker import _twin_worker
from .process_port import SpawnedPortProcess

_PRIVATE_SCENARIO_SEED_ENV = "CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED"


class NewtonSimulatedReality:
    """Reality service whose child process exclusively owns hidden parameters."""

    operations = frozenset(
        {
            LabOperation.FRICTION_EXPERIMENT.value,
            LabOperation.DEPLOY_POLICY.value,
            LabOperation.DEPLOY_CAPABILITY.value,
        }
    )
    evidence_kinds = frozenset(
        {
            LabArtifactKind.TRAJECTORY_DATASET.value,
            LabArtifactKind.REALITY_DEPLOYMENT.value,
        }
    )

    def __init__(
        self,
        artifact_root: Path,
        *,
        device: str = "cuda:0",
        seed: int = 7,
        timeout_s: float = 900.0,
    ) -> None:
        private_seed_override = os.environ.get(_PRIVATE_SCENARIO_SEED_ENV)
        self._worker = SpawnedPortProcess(
            target=_reality_worker,
            args=(artifact_root, device, seed, private_seed_override),
            name="curious-robot-reality",
            timeout_s=timeout_s,
            scrub_environment=(_PRIVATE_SCENARIO_SEED_ENV,),
        )
        self.identity = self._worker.identity

    def execute(self, request: OperationRequest) -> OperationResult:
        result = self._worker.call("execute", request)
        if not isinstance(result, OperationResult):
            raise TypeError("Reality worker returned an invalid operation result")
        return result

    def capture_evidence(self) -> tuple[ArtifactRef, ...]:
        return ()

    def close(self) -> None:
        self._worker.close()


class NewtonLearnedTwin:
    """Independent Twin service constructed only from a public WorldSpec."""

    operations = frozenset({LabOperation.BUILD_TWIN.value, LabOperation.VALIDATE_POLICY.value})
    evidence_kinds = frozenset(
        {LabArtifactKind.TWIN_BUILD.value, LabArtifactKind.TWIN_VALIDATION_REPORT.value}
    )

    def __init__(
        self,
        artifact_root: Path,
        *,
        device: str = "cuda:0",
        timeout_s: float = 900.0,
    ) -> None:
        self._worker = SpawnedPortProcess(
            target=_twin_worker,
            args=(artifact_root, device),
            name="curious-robot-twin",
            timeout_s=timeout_s,
            scrub_environment=(_PRIVATE_SCENARIO_SEED_ENV,),
        )
        self.identity = self._worker.identity

    def build(self, spec: WorldSpec) -> ArtifactRef:
        reference = self._worker.call("build", spec)
        if not isinstance(reference, ArtifactRef):
            raise TypeError("Twin worker returned an invalid build reference")
        return reference

    def execute(self, request: OperationRequest) -> OperationResult:
        result = self._worker.call("execute", request)
        if not isinstance(result, OperationResult):
            raise TypeError("Twin worker returned an invalid operation result")
        return result

    def close(self) -> None:
        self._worker.close()


class TerminalEvaluator:
    """Evaluator authority that reads only a terminal deployment artifact."""

    operations = frozenset({LabOperation.EVALUATE_TERMINAL.value})
    evidence_kinds = frozenset({LabArtifactKind.EVALUATION_REPORT.value})

    def __init__(self, artifact_root: Path, *, timeout_s: float = 60.0) -> None:
        self._worker = SpawnedPortProcess(
            target=_evaluator_worker,
            args=(artifact_root,),
            name="curious-robot-evaluator",
            timeout_s=timeout_s,
            scrub_environment=(_PRIVATE_SCENARIO_SEED_ENV,),
        )
        self.identity = self._worker.identity

    def evaluate_terminal(self, terminal_artifact: ArtifactRef) -> ArtifactRef:
        reference = self._worker.call(LabOperation.EVALUATE_TERMINAL.value, terminal_artifact)
        if not isinstance(reference, ArtifactRef):
            raise TypeError("Evaluator worker returned an invalid artifact reference")
        return reference

    def close(self) -> None:
        self._worker.close()
