"""Explicit state machine for evidence-gated real-to-sim-to-real runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    ArtifactEnvelope,
    ArtifactOutcome,
    ArtifactRef,
    ArtifactResolver,
    ArtifactVisibility,
    PipelineLane,
)


class PipelineState(StrEnum):
    CREATED = "created"
    CAPABILITY_GAP_ASSESSED = "capability_gap_assessed"
    REALITY_EVIDENCE_CAPTURED = "reality_evidence_captured"
    POSTERIOR_ESTIMATED = "posterior_estimated"
    TWIN_BUILT = "twin_built"
    TWIN_VALIDATED = "twin_validated"
    POLICY_VALIDATED = "policy_validated"
    REALITY_DEPLOYED = "reality_deployed"
    EVALUATED = "evaluated"
    FAILED = "failed"


TERMINAL_STATES = frozenset({PipelineState.EVALUATED, PipelineState.FAILED})

class IllegalPipelineTransition(ValueError):
    """The requested transition violates the evidence-gated workflow."""


@dataclass(frozen=True, slots=True)
class PipelineTransition:
    sequence: int
    previous: PipelineState
    current: PipelineState
    lane: PipelineLane
    evidence: tuple[ArtifactRef, ...]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionRule:
    target: PipelineState
    lane: PipelineLane
    evidence_kind: str
    visibility: ArtifactVisibility


_TRANSITION_RULES = {
    PipelineState.CREATED: TransitionRule(
        PipelineState.CAPABILITY_GAP_ASSESSED,
        PipelineLane.CAPABILITY_BUILD,
        "capability_gap",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.CAPABILITY_GAP_ASSESSED: TransitionRule(
        PipelineState.REALITY_EVIDENCE_CAPTURED,
        PipelineLane.REALITY,
        "trajectory_dataset",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.REALITY_EVIDENCE_CAPTURED: TransitionRule(
        PipelineState.POSTERIOR_ESTIMATED,
        PipelineLane.CAPABILITY_BUILD,
        "parameter_posterior",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.POSTERIOR_ESTIMATED: TransitionRule(
        PipelineState.TWIN_BUILT,
        PipelineLane.TWIN,
        "twin_build",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.TWIN_BUILT: TransitionRule(
        PipelineState.TWIN_VALIDATED,
        PipelineLane.TWIN,
        "twin_validation_report",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.TWIN_VALIDATED: TransitionRule(
        PipelineState.POLICY_VALIDATED,
        PipelineLane.DEPLOYMENT,
        "policy_artifact",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.POLICY_VALIDATED: TransitionRule(
        PipelineState.REALITY_DEPLOYED,
        PipelineLane.REALITY,
        "reality_deployment",
        ArtifactVisibility.AGENT,
    ),
    PipelineState.REALITY_DEPLOYED: TransitionRule(
        PipelineState.EVALUATED,
        PipelineLane.DEPLOYMENT,
        "evaluation_report",
        ArtifactVisibility.EVALUATOR,
    ),
}


class LaboratoryPipeline:
    def __init__(self, resolver: ArtifactResolver) -> None:
        self._resolver = resolver
        self._state = PipelineState.CREATED
        self._history: list[PipelineTransition] = []

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def history(self) -> tuple[PipelineTransition, ...]:
        return tuple(self._history)

    def transition(
        self,
        target: PipelineState,
        *,
        evidence: tuple[ArtifactRef, ...] = (),
        detail: str | None = None,
    ) -> PipelineTransition:
        rule = _TRANSITION_RULES.get(self._state)
        if self._state in TERMINAL_STATES or (
            target is not PipelineState.FAILED and (rule is None or target is not rule.target)
        ):
            raise IllegalPipelineTransition(f"cannot transition {self._state.value} -> {target.value}")
        if target is PipelineState.FAILED and not evidence:
            raise IllegalPipelineTransition("failure transition requires a preserved FAIL artifact")
        if target is PipelineState.FAILED and any(
            item.outcome is not ArtifactOutcome.FAIL for item in evidence
        ):
            raise IllegalPipelineTransition("failure transition evidence must be FAIL artifacts")
        if target is not PipelineState.FAILED and not evidence:
            raise IllegalPipelineTransition("successful transition requires evidence")
        if target is not PipelineState.FAILED and any(
            item.outcome is not ArtifactOutcome.PASS for item in evidence
        ):
            raise IllegalPipelineTransition("successful transition evidence must be PASS artifacts")
        resolved_evidence: list[ArtifactEnvelope] = []
        for reference in evidence:
            try:
                resolved = self._resolver.get(reference)
            except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as error:
                raise IllegalPipelineTransition(
                    f"transition evidence is not present and valid: {reference.artifact_id}"
                ) from error
            if resolved.ref() != reference:
                raise IllegalPipelineTransition("resolved transition evidence does not match its ref")
            resolved_evidence.append(resolved)
        if target is not PipelineState.FAILED:
            candidates = [
                envelope
                for envelope in resolved_evidence
                if envelope.kind == rule.evidence_kind and envelope.visibility is rule.visibility
            ]
            if not candidates:
                raise IllegalPipelineTransition(
                    f"{target.value} requires {rule.visibility.value} {rule.evidence_kind} evidence"
                )
            if len(candidates) != len(resolved_evidence):
                raise IllegalPipelineTransition(
                    f"all {target.value} evidence must be {rule.visibility.value} "
                    f"{rule.evidence_kind} artifacts"
                )
            if self._history:
                prior_ids = {item.artifact_id for item in self._history[-1].evidence}
                if not any(
                    prior_ids.issubset({item.artifact_id for item in candidate.provenance})
                    for candidate in candidates
                ):
                    raise IllegalPipelineTransition(
                        f"{rule.evidence_kind} must directly reference the prior transition evidence"
                    )
        previous = self._state
        transition = PipelineTransition(
            sequence=len(self._history) + 1,
            previous=previous,
            current=target,
            lane=rule.lane if rule is not None else PipelineLane.CAPABILITY_BUILD,
            evidence=evidence,
            detail=detail,
        )
        self._state = target
        self._history.append(transition)
        return transition
