"""Parameter priors and immutable belief updates."""

from __future__ import annotations

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
