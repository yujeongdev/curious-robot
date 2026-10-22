import pytest
from pydantic import ValidationError

from curious_robot.protocol import PhysicalExperimentProtocol, PushProtocol, load_default_protocol


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_horizontal_force_n", float("inf")),
        ("contact_speed_m_s", float("nan")),
        ("lateral_ik_compensation_m", float("-inf")),
    ],
)
def test_push_protocol_rejects_non_finite_physical_values(field: str, value: float) -> None:
    payload = load_default_protocol().final_push.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        PushProtocol.model_validate(payload)


def test_schema_1_1_protocol_artifacts_migrate_controller_defaults() -> None:
    payload = load_default_protocol().model_dump()
    for section in ("static_probe", "dynamic_probe", "final_push"):
        for field in (
            "minimum_planar_command_m",
            "opposing_surface_max_dot",
            "post_attempt_settle_s",
            "terminal_settle_s",
        ):
            payload[section].pop(field)

    restored = PhysicalExperimentProtocol.model_validate(payload)

    assert restored.schema_version == "1.1.0"
    assert restored.final_push.minimum_planar_command_m == pytest.approx(1.0e-6)
    assert restored.final_push.opposing_surface_max_dot == pytest.approx(-0.8)
    assert restored.final_push.post_attempt_settle_s == pytest.approx(0.15)
    assert restored.final_push.terminal_settle_s == pytest.approx(0.35)
