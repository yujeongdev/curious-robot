import pytest
from pydantic import ValidationError

from curious_robot.belief import initial_belief, update_belief
from curious_robot.schemas import ActionCandidate, ParameterEstimate, ParameterName


def test_public_belief_has_no_truth_fields() -> None:
    serialized = initial_belief().model_dump_json()
    assert "truth" not in serialized
    assert "hidden" not in serialized


def test_belief_update_is_immutable_and_tracks_evidence() -> None:
    prior = initial_belief()
    estimate = ParameterEstimate(
        name=ParameterName.MASS,
        mean=0.2,
        std=0.01,
        interval95=(0.18, 0.22),
        physical_bounds=(0.08, 0.45),
        units="kg",
        status="identified",
        evidence_ids=("mass-1",),
        method="test",
    )
    posterior = update_belief(prior, estimate)
    assert prior.revision == 0
    assert posterior.revision == 1
    assert posterior.parameters[ParameterName.MASS].mean == 0.2
    assert posterior.supporting_evidence_ids == ("mass-1",)


@pytest.mark.parametrize(
    ("speed_m_s", "max_force_n", "push_height_m"),
    [(-0.01, 5.0, 0.01), (0.2, 5.0, 0.01), (0.04, float("nan"), 0.01), (0.04, 5.0, -0.01)],
)
def test_action_candidate_rejects_unsafe_motion_values(
    speed_m_s: float,
    max_force_n: float,
    push_height_m: float,
) -> None:
    with pytest.raises(ValidationError):
        ActionCandidate(
            action_id="unsafe",
            kind="push",
            target_m=(0.1, 0.0, 0.025),
            speed_m_s=speed_m_s,
            max_force_n=max_force_n,
            push_height_m=push_height_m,
        )
