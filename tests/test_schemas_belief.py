from curious_robot.belief import initial_belief, update_belief
from curious_robot.schemas import ParameterEstimate, ParameterName


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
