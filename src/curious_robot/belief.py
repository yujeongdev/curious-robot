"""Parameter priors and immutable belief updates."""

from __future__ import annotations

from .objects import get_object_archetype
from .schemas import ParameterEstimate, ParameterName, PhysicalBelief


def initial_belief() -> PhysicalBelief:
    """Broad priors covering the randomized evaluation envelope."""
    return PhysicalBelief(
        parameters={
            ParameterName.MASS: _prior(ParameterName.MASS, 0.265, 0.107, (0.08, 0.45), "kg"),
            ParameterName.STATIC_FRICTION: _prior(
                ParameterName.STATIC_FRICTION, 0.55, 0.202, (0.20, 0.90), "1"
            ),
            ParameterName.DYNAMIC_FRICTION: _prior(
                ParameterName.DYNAMIC_FRICTION, 0.35, 0.144, (0.10, 0.60), "1"
            ),
            ParameterName.COM_X: _prior(ParameterName.COM_X, 0.0, 0.005, (-0.015, 0.015), "m"),
            ParameterName.COM_Y: _prior(ParameterName.COM_Y, 0.0, 0.005, (-0.015, 0.015), "m"),
            ParameterName.COM_Z: _prior(ParameterName.COM_Z, 0.0, 0.003, (-0.010, 0.010), "m"),
        }
    )


def initial_belief_for_object(archetype_id: str, version: str) -> PhysicalBelief:
    """Return broad material priors plus the object's public uniform-density COM."""

    belief = initial_belief()
    archetype = get_object_archetype(archetype_id, version)
    parameters = dict(belief.parameters)
    for name, value in zip(
        (ParameterName.COM_X, ParameterName.COM_Y, ParameterName.COM_Z),
        archetype.uniform_density_centroid_m,
        strict=True,
    ):
        parameters[name] = ParameterEstimate(
            name=name,
            mean=value,
            std=0.0,
            interval95=(value, value),
            physical_bounds=(value, value),
            units="m",
            status="bounded",
            method="public uniform-density geometry assumption",
            assumptions=("uniform density; not identified from episode evidence",),
        )
    return belief.model_copy(
        update={
            "parameters": parameters,
            "assumptions": (
                f"rigid {archetype.archetype_id} with known public geometry",
                "uniform-density COM fixed from public geometry",
                "horizontal stationary table",
                "Coulomb friction is an effective model",
                "yaw inertia is not independently identified",
            ),
        }
    )


def _prior(
    name: ParameterName, mean: float, std: float, bounds: tuple[float, float], units: str
) -> ParameterEstimate:
    return ParameterEstimate(
        name=name,
        mean=mean,
        std=std,
        interval95=bounds,
        physical_bounds=bounds,
        units=units,
        status="prior",
        method="bounded uniform prior",
    )


def update_belief(belief: PhysicalBelief, estimate: ParameterEstimate) -> PhysicalBelief:
    parameters = dict(belief.parameters)
    parameters[estimate.name] = estimate
    evidence = tuple(dict.fromkeys((*belief.supporting_evidence_ids, *estimate.evidence_ids)))
    return belief.model_copy(
        update={
            "revision": belief.revision + 1,
            "parameters": parameters,
            "supporting_evidence_ids": evidence,
        }
    )


def belief_value(belief: PhysicalBelief, name: ParameterName) -> float:
    return belief.parameters[name].mean
