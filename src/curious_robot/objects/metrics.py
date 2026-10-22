"""Pose-registration metrics derived only from public object geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .contracts import ObjectArchetype


@dataclass(frozen=True, slots=True)
class PlanarShapeError:
    maximum_corner_error_m: float
    rms_corner_error_m: float


def registered_planar_corner_error(
    archetype: ObjectArchetype,
    *,
    observed_xy_yaw: tuple[float, float, float],
    target_xy_yaw: tuple[float, float, float],
) -> PlanarShapeError:
    """Compare corresponding public box corners after two planar transforms."""

    local_corners = np.asarray(
        [
            (
                part.center_object_m[0] + x_sign * part.half_extents_m[0],
                part.center_object_m[1] + y_sign * part.half_extents_m[1],
            )
            for part in archetype.parts
            for x_sign in (-1.0, 1.0)
            for y_sign in (-1.0, 1.0)
        ],
        dtype=float,
    )

    def transform(pose: tuple[float, float, float]) -> np.ndarray:
        x_m, y_m, yaw_rad = pose
        cosine = math.cos(yaw_rad)
        sine = math.sin(yaw_rad)
        rotation = np.asarray(((cosine, -sine), (sine, cosine)), dtype=float)
        return local_corners @ rotation.T + np.asarray((x_m, y_m), dtype=float)

    corner_errors = np.linalg.norm(transform(observed_xy_yaw) - transform(target_xy_yaw), axis=1)
    return PlanarShapeError(
        maximum_corner_error_m=float(np.max(corner_errors)),
        rms_corner_error_m=float(np.sqrt(np.mean(np.square(corner_errors)))),
    )
