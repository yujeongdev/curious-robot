import pytest
from pydantic import ValidationError

from curious_robot.protocol import (
    PhysicalExperimentProtocol,
    PushProtocol,
    load_default_protocol,
)


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


def test_schema_1_1_protocol_artifacts_are_rejected_after_controller_contract_change() -> None:
    payload = load_default_protocol().model_dump()
    payload["schema_version"] = "1.1.0"
    for section in ("static_probe", "dynamic_probe", "final_push"):
        for field in (
            "minimum_planar_command_m",
            "opposing_surface_max_dot",
            "post_attempt_settle_s",
            "terminal_settle_s",
        ):
            payload[section].pop(field)

    with pytest.raises(ValidationError):
        PhysicalExperimentProtocol.model_validate(payload)
