import pytest

from curious_robot.trajectory import (
    minimum_jerk_blend,
    minimum_jerk_duration_s,
    physics_substeps_for_target_speed,
)


def test_minimum_jerk_duration_bounds_peak_scalar_speed() -> None:
    duration_s = minimum_jerk_duration_s(distance=0.12, peak_speed=0.08)

    assert duration_s == pytest.approx(2.8125)
    assert minimum_jerk_blend(0.0) == 0.0
    assert minimum_jerk_blend(1.0) == 1.0


def test_physics_substeps_follow_requested_contact_speed() -> None:
    slow = physics_substeps_for_target_speed(
        increment_m=0.001,
        speed_m_s=0.02,
        dt_s=1.0 / 240.0,
    )
    fast = physics_substeps_for_target_speed(
        increment_m=0.001,
        speed_m_s=0.04,
        dt_s=1.0 / 240.0,
    )

    assert slow == 12
    assert fast == 6

    rounded_up = physics_substeps_for_target_speed(
        increment_m=0.0005,
        speed_m_s=0.09,
        dt_s=1.0 / 240.0,
    )
    actual_speed = 0.0005 / (rounded_up * (1.0 / 240.0))
    assert actual_speed <= 0.09


@pytest.mark.parametrize(
    ("distance", "speed"),
    [(-0.1, 0.1), (0.1, 0.0), (float("nan"), 0.1)],
)
def test_minimum_jerk_duration_rejects_invalid_operating_points(distance: float, speed: float) -> None:
    with pytest.raises(ValueError):
        minimum_jerk_duration_s(distance, speed)
