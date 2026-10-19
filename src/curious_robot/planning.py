"""Deterministic active-experiment and action planning."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .schemas import (
    ActionCandidate,
    ActionDecision,
    ExperimentCandidate,
    ExperimentPlan,
    ExperimentScore,
    ParameterName,
    PhysicalBelief,
    PredictedOutcome,
)

DEFAULT_EXPERIMENTS = (
    ExperimentCandidate(
        candidate_id="weigh_quasistatic",
        experiment="weigh",
        targets=(ParameterName.MASS,),
        expected_std_fraction=0.12,
        duration_s=2.5,
        cost=0.10,
        risk=0.12,
        max_force_n=12.0,
    ),
    ExperimentCandidate(
        candidate_id="static_breakaway_ramp",
        experiment="probe_static",
        targets=(ParameterName.STATIC_FRICTION,),
        expected_std_fraction=0.18,
        duration_s=2.0,
        cost=0.08,
        risk=0.08,
        max_force_n=8.0,
    ),
    ExperimentCandidate(
        candidate_id="dynamic_controlled_slide",
        experiment="probe_dynamic",
        targets=(ParameterName.DYNAMIC_FRICTION,),
        expected_std_fraction=0.20,
        duration_s=2.2,
        cost=0.10,
        risk=0.10,
        max_force_n=8.0,
    ),
    ExperimentCandidate(
        candidate_id="multi_orientation_com",
        experiment="estimate_com",
        targets=(ParameterName.COM_X, ParameterName.COM_Y, ParameterName.COM_Z),
        expected_std_fraction=0.30,
        duration_s=4.0,
        cost=0.25,
        risk=0.25,
        max_force_n=12.0,
    ),
)


def estimate_parameter_uncertainty(belief: PhysicalBelief) -> dict[str, float]:
    """Return each posterior standard deviation normalized by prior range."""
    return {
        name.value: estimate.std / max(estimate.physical_bounds[1] - estimate.physical_bounds[0], 1e-9)
        for name, estimate in belief.parameters.items()
    }


def evaluate_information_gain(
    candidate: ExperimentCandidate,
    belief: PhysicalBelief,
    relevance: Mapping[ParameterName, float],
    *,
    max_force_n: float = 15.0,
) -> ExperimentScore:
    """Closed-form Gaussian entropy reduction with explicit costs and safety filter."""
    reasons: list[str] = []
    if candidate.max_force_n > max_force_n:
        reasons.append("candidate exceeds measured-force safety limit")
    gains = []
    for target in candidate.targets:
        current = belief.parameters[target]
        prior_range = current.physical_bounds[1] - current.physical_bounds[0]
        predicted_std = max(candidate.expected_std_fraction * prior_range, 1e-6)
        if current.std <= predicted_std:
            gains.append(0.0)
        else:
            gains.append(relevance.get(target, 0.0) * math.log(current.std / predicted_std))
    weighted_gain = float(sum(gains))
    cost_penalty = 0.018 * candidate.duration_s + 0.10 * candidate.cost
    risk_penalty = 0.20 * candidate.risk
    feasible = not reasons
    utility = weighted_gain - cost_penalty - risk_penalty if feasible else -math.inf
    return ExperimentScore(
        candidate_id=candidate.candidate_id,
        expected_information_gain=float(
            sum(
                g / max(relevance.get(t, 1e-12), 1e-12)
                for g, t in zip(gains, candidate.targets, strict=True)
            )
        ),
        relevance_weighted_gain=weighted_gain,
        cost_penalty=cost_penalty,
        risk_penalty=risk_penalty,
        feasible=feasible,
        rejection_reasons=tuple(reasons),
        utility=utility,
    )


def propose_experiment(
    belief: PhysicalBelief,
    relevance: Mapping[ParameterName, float],
    completed: set[str] | None = None,
    candidates: Sequence[ExperimentCandidate] = DEFAULT_EXPERIMENTS,
) -> ExperimentPlan:
    completed = completed or set()
    available = [c for c in candidates if c.candidate_id not in completed]
    scores = tuple(evaluate_information_gain(c, belief, relevance) for c in available)
    feasible = sorted(
        (s for s in scores if s.feasible and s.utility > 0), key=lambda s: (-s.utility, s.candidate_id)
    )
    selected = feasible[0].candidate_id if feasible else None
    rationale = (
        f"selected {selected}: highest positive relevance-weighted entropy reduction after cost and risk"
        if selected
        else "stop experimenting: no safe candidate has positive expected utility"
    )
    return ExperimentPlan(selected=selected, scores=scores, rationale=rationale)


def candidate_actions(target_m: tuple[float, float, float]) -> tuple[ActionCandidate, ...]:
    return (
        ActionCandidate(
            action_id="push_straight_low",
            kind="push",
            target_m=target_m,
            speed_m_s=0.04,
            max_force_n=12.0,
            push_height_m=0.015,
        ),
        ActionCandidate(
            action_id="pick_and_place",
            kind="pick_place",
            target_m=target_m,
            speed_m_s=0.03,
            max_force_n=12.0,
        ),
    )


def select_next_action(outcomes: Sequence[PredictedOutcome]) -> ActionDecision:
    feasible = [o for o in outcomes if o.feasible]
    if not feasible:
        raise RuntimeError("no feasible manipulation action")

    def utility(o: PredictedOutcome) -> float:
        return (
            o.success_probability
            - 4.0 * o.target_error_m
            - 0.010 * o.peak_force_n
            - 0.015 * o.duration_s
            + 0.05 * max(o.tipping_margin, -1.0)
        )

    selected = sorted(feasible, key=lambda o: (-utility(o), o.action_id))[0]
    return ActionDecision(
        selected_action_id=selected.action_id,
        outcomes=tuple(outcomes),
        rationale=(
            f"{selected.action_id} maximizes robust belief-rollout utility over success, target error, "
            "peak force, duration, and tipping margin"
        ),
    )


def task_relevance_for_actions(actions: Sequence[ActionCandidate]) -> dict[ParameterName, float]:
    """Finite-difference-inspired baseline relevance for currently available actions."""
    has_push = any(a.kind == "push" for a in actions)
    has_pick = any(a.kind == "pick_place" for a in actions)
    return {
        ParameterName.MASS: 0.65 if has_pick else 0.15,
        ParameterName.STATIC_FRICTION: 1.0 if has_push else 0.10,
        ParameterName.DYNAMIC_FRICTION: 0.80 if has_push else 0.05,
        ParameterName.COM_X: 0.08 if has_pick else 0.01,
        ParameterName.COM_Y: 0.08 if has_pick else 0.01,
        ParameterName.COM_Z: 0.08 if has_pick else 0.01,
    }
