"""Hidden surface-profile sampling for the simulated-reality evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class SurfaceFamily(StrEnum):
    WOOD = "wood"
    RUBBER_MAT = "rubber_mat"
    COATED_METAL = "coated_metal"
    POLYMER = "polymer"


@dataclass(frozen=True, slots=True)
class SurfaceProfile:
    """Evaluator-only physical and visual parameters.

    The friction values describe the effective object-surface interaction. They
    are not separately identifiable object and table material coefficients.
    """

    family: SurfaceFamily
    effective_static_friction: float
    effective_dynamic_friction: float
    restitution: float
    visual_roughness: float
    visual_base_color_rgb: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class _FamilyBounds:
    static: tuple[float, float]
    dynamic: tuple[float, float]
    restitution: tuple[float, float]
    roughness: tuple[float, float]
    color: tuple[float, float, float]


_BOUNDS = {
    SurfaceFamily.WOOD: _FamilyBounds(
        (0.30, 0.62), (0.18, 0.48), (0.02, 0.10), (0.45, 0.78), (0.48, 0.29, 0.15)
    ),
    SurfaceFamily.RUBBER_MAT: _FamilyBounds(
        (0.62, 0.90), (0.42, 0.60), (0.00, 0.05), (0.70, 0.90), (0.10, 0.12, 0.13)
    ),
    SurfaceFamily.COATED_METAL: _FamilyBounds(
        (0.20, 0.48), (0.10, 0.34), (0.04, 0.15), (0.25, 0.52), (0.34, 0.38, 0.42)
    ),
    SurfaceFamily.POLYMER: _FamilyBounds(
        (0.34, 0.72), (0.20, 0.52), (0.02, 0.12), (0.32, 0.68), (0.16, 0.34, 0.44)
    ),
}


def sample_surface_profile(seed: int, family: SurfaceFamily | None = None) -> SurfaceProfile:
    """Sample a reproducible hidden surface profile within physical bounds."""
    rng = np.random.default_rng(seed)
    selected = family or tuple(SurfaceFamily)[int(rng.integers(0, len(SurfaceFamily)))]
    bounds = _BOUNDS[selected]
    mu_static = float(rng.uniform(*bounds.static))
    dynamic_upper = min(bounds.dynamic[1], mu_static - 0.03)
    mu_dynamic = float(rng.uniform(bounds.dynamic[0], dynamic_upper))
    return SurfaceProfile(
        family=selected,
        effective_static_friction=mu_static,
        effective_dynamic_friction=mu_dynamic,
        restitution=float(rng.uniform(*bounds.restitution)),
        visual_roughness=float(rng.uniform(*bounds.roughness)),
        visual_base_color_rgb=bounds.color,
    )


def surface_family_bounds() -> dict[SurfaceFamily, dict[str, tuple[float, float]]]:
    """Return documentation/test bounds, never an episode's sampled truth."""
    return {
        family: {
            "effective_static_friction": bounds.static,
            "effective_dynamic_friction": bounds.dynamic,
            "restitution": bounds.restitution,
            "visual_roughness": bounds.roughness,
        }
        for family, bounds in _BOUNDS.items()
    }
