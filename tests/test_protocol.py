import pytest
from pydantic import ValidationError

from curious_robot.protocol import PushProtocol, load_default_protocol


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
