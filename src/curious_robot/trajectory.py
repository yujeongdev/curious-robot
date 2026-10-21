"""Hardware-neutral minimum-jerk trajectory primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass

MINIMUM_JERK_PEAK_VELOCITY_FACTOR = 1.875


class CartesianTrackingError(RuntimeError):
    """Raised when a Cartesian command cannot reach its target within tolerance."""


@dataclass(frozen=True, slots=True)
class CartesianMotionResult:
    physics_steps: int
    duration_s: float
    tracking_error_m: float


def minimum_jerk_blend(phase: float) -> float:
    """Quintic blend with zero endpoint velocity and acceleration."""

    bounded = min(max(phase, 0.0), 1.0)
    return 10.0 * bounded**3 - 15.0 * bounded**4 + 6.0 * bounded**5


def minimum_jerk_duration_s(distance: float, peak_speed: float) -> float:
    """Duration that bounds peak speed for a minimum-jerk scalar path."""

    if not math.isfinite(distance) or distance < 0.0:
        raise ValueError("distance must be finite and non-negative")
    if not math.isfinite(peak_speed) or peak_speed <= 0.0:
        raise ValueError("peak speed must be finite and positive")
    return MINIMUM_JERK_PEAK_VELOCITY_FACTOR * distance / peak_speed


def physics_substeps_for_target_speed(
    *,
    increment_m: float,
    speed_m_s: float,
    dt_s: float,
) -> int:
    """Minimum substep count whose target ramp does not exceed the requested speed."""

    if not all(math.isfinite(value) and value > 0.0 for value in (increment_m, speed_m_s, dt_s)):
        raise ValueError("increment, speed, and timestep must be finite and positive")
    return max(1, math.ceil(increment_m / (speed_m_s * dt_s)))
