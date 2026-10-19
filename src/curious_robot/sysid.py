"""Observation-only physical estimators and black-box calibration."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
from scipy.optimize import least_squares

from .schemas import ExperimentEvidence, ParameterEstimate, ParameterName

GRAVITY = 9.81


def estimate_mass(evidence: ExperimentEvidence, noise_floor_fraction: float = 0.02) -> ParameterEstimate:
    """Estimate mass from stable lift actuator reaction after subtracting tare."""
    tare = float(evidence.metadata.get("tare_force_z_n", 0.0))
    stable = [s for s in evidence.samples if abs(s.velocity_m_s[2]) < 0.01]
    if len(stable) < 3:
        return _unidentifiable(ParameterName.MASS, (0.08, 0.45), "kg", evidence, "insufficient stable lift")
    masses = np.asarray([abs(s.measured_force_n[2] - tare) / GRAVITY for s in stable])
    median = float(np.median(masses))
    mad = float(1.4826 * np.median(np.abs(masses - median)))
    std = max(mad / math.sqrt(len(masses)), noise_floor_fraction * median, 1e-4)
    lo, hi = max(0.08, median - 1.96 * std), min(0.45, median + 1.96 * std)
    return ParameterEstimate(
        name=ParameterName.MASS,
        mean=float(np.clip(median, 0.08, 0.45)),
        std=std,
        interval95=(lo, hi),
        physical_bounds=(0.08, 0.45),
        units="kg",
        status="identified",
        evidence_ids=(evidence.evidence_id,),
        method="tare-corrected quasi-static load / gravity; robust median",
        assumptions=("quasi-static lift", "world-frame vertical wrench"),
    )


def estimate_static_friction(evidence: ExperimentEvidence, mass: ParameterEstimate) -> ParameterEstimate:
    """Return an interval-censored coefficient from measured breakaway force."""
    motion_threshold = float(evidence.metadata.get("motion_threshold_m_s", 0.01))
    moving = [i for i, s in enumerate(evidence.samples) if abs(s.velocity_m_s[0]) >= motion_threshold]
    if not moving:
        hold = max((abs(s.measured_force_n[0]) for s in evidence.samples), default=0.0)
        lo = float(np.clip(hold / (mass.interval95[1] * GRAVITY), 0.2, 0.9))
        return ParameterEstimate(
            name=ParameterName.STATIC_FRICTION,
            mean=(lo + 0.9) / 2,
            std=(0.9 - lo) / math.sqrt(12),
            interval95=(lo, 0.9),
            physical_bounds=(0.2, 0.9),
            units="1",
            status="bounded",
            evidence_ids=(evidence.evidence_id,),
            method="right-censored measured breakaway force",
            assumptions=("horizontal table",),
        )
    first = moving[0]
    pre = evidence.samples[:first]
    hold = max((abs(s.measured_force_n[0]) for s in pre), default=0.0)
    brk = abs(evidence.samples[first].measured_force_n[0])
    # Preserve coverage under finite force increments and sensor noise rather
    # than reporting an unjustifiably sharp contact-event boundary.
    discretization_margin = float(evidence.metadata.get("force_increment_n", 0.0)) / (
        max(mass.mean, 1e-6) * GRAVITY
    )
    lo = float(np.clip(hold / (mass.interval95[1] * GRAVITY) - discretization_margin, 0.2, 0.9))
    hi = float(
        np.clip(
            brk / (max(mass.interval95[0], 1e-6) * GRAVITY) + discretization_margin,
            lo,
            0.9,
        )
    )
    mid = (lo + hi) / 2
    return ParameterEstimate(
        name=ParameterName.STATIC_FRICTION,
        mean=mid,
        std=max((hi - lo) / math.sqrt(12), 0.01),
        interval95=(lo, hi),
        physical_bounds=(0.2, 0.9),
        units="1",
        status="bounded",
        evidence_ids=(evidence.evidence_id,),
        method="interval-censored measured breakaway force / normal load",
        assumptions=("Coulomb breakaway", "horizontal push"),
    )


def estimate_dynamic_friction(
    evidence: ExperimentEvidence,
    mass: ParameterEstimate,
    static_friction: ParameterEstimate,
) -> ParameterEstimate:
    samples = [s for s in evidence.samples if abs(s.velocity_m_s[0]) > 0.015]
    if len(samples) < 5:
        return _unidentifiable(
            ParameterName.DYNAMIC_FRICTION, (0.10, 0.60), "1", evidence, "insufficient sliding samples"
        )
    t = np.asarray([s.timestamp_s for s in samples])
    velocity = np.asarray([s.velocity_m_s[0] for s in samples])
    acceleration = np.gradient(velocity, t)
    force = np.asarray([s.measured_force_n[0] for s in samples])
    normal = np.asarray([s.normal_load_n for s in samples])
    m = mass.mean

    def residual(x: np.ndarray) -> np.ndarray:
        return force - m * acceleration - x[0] * normal * np.sign(velocity)

    upper = min(0.60, static_friction.mean)
    fit = least_squares(residual, x0=[min(0.35, upper)], bounds=([0.10], [upper]), loss="soft_l1")
    mu = float(fit.x[0])
    residual_std = float(np.std(residual(fit.x), ddof=1)) if len(samples) > 1 else 0.0
    info = float(np.sum(normal**2))
    std = max(residual_std / math.sqrt(max(info, 1e-9)), 0.01)
    mismatch = ()
    if residual_std > max(0.15, 0.15 * float(np.median(np.abs(force)))):
        mismatch = ("Coulomb residual is structured or larger than the validation threshold",)
    return ParameterEstimate(
        name=ParameterName.DYNAMIC_FRICTION,
        mean=mu,
        std=std,
        interval95=(max(0.10, mu - 1.96 * std), min(upper, mu + 1.96 * std)),
        physical_bounds=(0.10, 0.60),
        units="1",
        status="identified",
        evidence_ids=(evidence.evidence_id,),
        method="bounded robust least squares: F = m a + mu N sign(v)",
        assumptions=("effective Coulomb sliding friction",),
        mismatch_notes=mismatch,
    )


def calibrate_effective_friction(
    observed_positions: Sequence[float],
    simulate: Callable[[float], Sequence[float]],
    initial_mu: float,
    bounds: tuple[float, float] = (0.1, 0.9),
) -> tuple[float, float]:
    """Fit an effective simulation parameter without assuming contact gradients."""
    observed = np.asarray(observed_positions, dtype=float)

    def residual(x: np.ndarray) -> np.ndarray:
        return np.asarray(simulate(float(x[0])), dtype=float) - observed

    result = least_squares(residual, [initial_mu], bounds=([bounds[0]], [bounds[1]]), loss="soft_l1")
    rmse = float(np.sqrt(np.mean(residual(result.x) ** 2)))
    return float(result.x[0]), rmse


def calibrate_coulomb_surrogate(
    evidence: ExperimentEvidence,
    mass_kg: float,
    initial_mu: float,
    bounds: tuple[float, float] = (0.1, 0.9),
) -> dict[str, float | str | bool]:
    """Fit an effective mu against a held-out 1-D trajectory surrogate.

    This is deliberately not presented as differentiable MuJoCo contact. The
    surrogate integrates the measured actuator force through a Coulomb model;
    SciPy supplies finite-difference Jacobians for a bounded robust fit.
    """
    samples = [sample for sample in evidence.samples if sample.velocity_m_s[0] > 0.015]
    if len(samples) < 12:
        return {"success": False, "reason": "insufficient sliding samples"}
    split = max(8, int(len(samples) * 2 / 3))

    def rollout(mu: float, subset: Sequence) -> np.ndarray:
        predicted = [subset[0].position_m[0]]
        velocity = subset[0].velocity_m_s[0]
        for previous, current in zip(subset[:-1], subset[1:], strict=True):
            dt = current.timestamp_s - previous.timestamp_s
            acceleration = (previous.measured_force_n[0] - mu * mass_kg * GRAVITY) / mass_kg
            velocity += acceleration * dt
            predicted.append(predicted[-1] + velocity * dt)
        return np.asarray(predicted)

    training = samples[:split]
    observed_train = np.asarray([sample.position_m[0] for sample in training])
    fit = least_squares(
        lambda x: (rollout(float(x[0]), training) - observed_train) / 0.002,
        [initial_mu],
        bounds=([bounds[0]], [bounds[1]]),
        loss="soft_l1",
    )
    mu = float(fit.x[0])
    train_rmse = float(np.sqrt(np.mean((rollout(mu, training) - observed_train) ** 2)))
    validation = samples[split - 1 :]
    observed_validation = np.asarray([sample.position_m[0] for sample in validation])
    validation_rmse = float(np.sqrt(np.mean((rollout(mu, validation) - observed_validation) ** 2)))
    return {
        "success": bool(fit.success),
        "optimizer": "scipy.optimize.least_squares(soft_l1, finite_difference)",
        "effective_dynamic_friction": mu,
        "analytic_dynamic_friction": initial_mu,
        "train_position_rmse_m": train_rmse,
        "validation_position_rmse_m": validation_rmse,
        "evaluations": int(fit.nfev),
        "model": "1-D Coulomb trajectory surrogate; numerical/contact settings fixed",
    }


def _unidentifiable(
    name: ParameterName,
    bounds: tuple[float, float],
    units: str,
    evidence: ExperimentEvidence,
    reason: str,
) -> ParameterEstimate:
    return ParameterEstimate(
        name=name,
        mean=sum(bounds) / 2,
        std=(bounds[1] - bounds[0]) / math.sqrt(12),
        interval95=bounds,
        physical_bounds=bounds,
        units=units,
        status="unidentifiable",
        evidence_ids=(evidence.evidence_id,),
        method="no estimate",
        mismatch_notes=(reason,),
    )
