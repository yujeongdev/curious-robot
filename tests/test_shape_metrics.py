import math

import pytest

from curious_robot.objects import T_BAR_V1, registered_planar_corner_error


def test_registered_t_bar_corner_error_is_zero_for_matching_pose() -> None:
    error = registered_planar_corner_error(
        T_BAR_V1,
        observed_xy_yaw=(0.15, 0.0, 0.0),
        target_xy_yaw=(0.15, 0.0, 0.0),
    )

    assert error.maximum_corner_error_m == pytest.approx(0.0)
    assert error.rms_corner_error_m == pytest.approx(0.0)


def test_registered_t_bar_corner_error_captures_translation_and_yaw() -> None:
    translation = registered_planar_corner_error(
        T_BAR_V1,
        observed_xy_yaw=(0.16, 0.0, 0.0),
        target_xy_yaw=(0.15, 0.0, 0.0),
    )
    rotation = registered_planar_corner_error(
        T_BAR_V1,
        observed_xy_yaw=(0.15, 0.0, math.radians(5.0)),
        target_xy_yaw=(0.15, 0.0, 0.0),
    )

    assert translation.maximum_corner_error_m == pytest.approx(0.01)
    assert translation.rms_corner_error_m == pytest.approx(0.01)
    assert rotation.maximum_corner_error_m > 0.0
    assert rotation.maximum_corner_error_m >= rotation.rms_corner_error_m
