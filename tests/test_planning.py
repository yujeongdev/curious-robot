from curious_robot.belief import initial_belief
from curious_robot.planning import (
    DEFAULT_EXPERIMENTS,
    candidate_actions,
    evaluate_information_gain,
    propose_experiment,
    select_next_action,
)
from curious_robot.schemas import ParameterName, PredictedOutcome


def test_planner_prioritizes_task_relevant_friction_probe() -> None:
    relevance = {name: 0.0 for name in ParameterName}
    relevance[ParameterName.STATIC_FRICTION] = 1.0
    plan = propose_experiment(initial_belief(), relevance)
    assert plan.selected == "static_breakaway_ramp"


def test_force_limit_is_a_hard_filter() -> None:
    score = evaluate_information_gain(
        DEFAULT_EXPERIMENTS[0],
        initial_belief(),
        {ParameterName.MASS: 1.0},
        max_force_n=5.0,
    )
    assert not score.feasible
    assert score.utility == -float("inf")


def test_action_selection_is_input_order_invariant() -> None:
    push = outcome("push", error=0.01, probability=0.9, force=3.0)
    pick = outcome("pick", error=0.02, probability=0.85, force=4.0)
    assert (
        select_next_action([push, pick]).selected_action_id
        == select_next_action([pick, push]).selected_action_id
    )


def test_t_bar_task_exposes_three_independent_push_candidates() -> None:
    actions = candidate_actions((0.15, 0.0, 0.02), task_id="push-t-bar-v1")
    assert len(actions) == 3
    assert len({action.action_id for action in actions}) == 3
    assert {action.kind for action in actions} == {"push"}
    assert len({(action.speed_m_s, action.max_force_n, action.push_height_m) for action in actions}) == 3


def outcome(action_id: str, error: float, probability: float, force: float) -> PredictedOutcome:
    return PredictedOutcome(
        action_id=action_id,
        final_position_m=(0.0, 0.0, 0.025),
        target_error_m=error,
        success_probability=probability,
        tipping_margin=0.2,
        peak_force_n=force,
        duration_s=2.0,
        feasible=True,
        rationale="fixture",
    )
