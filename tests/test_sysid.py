from curious_robot.schemas import ExperimentEvidence, MotionSample, ParameterEstimate, ParameterName
from curious_robot.sysid import (
    GRAVITY,
    calibrate_coulomb_surrogate,
    estimate_dynamic_friction,
    estimate_mass,
    estimate_static_friction,
)


def sample(t: float, *, force_x: float = 0.0, force_z: float = 0.0, vx: float = 0.0) -> MotionSample:
    return MotionSample(
        timestamp_s=t,
        position_m=(t * vx, 0.0, 0.025),
        velocity_m_s=(vx, 0.0, 0.0),
        measured_force_n=(force_x, 0.0, force_z),
        normal_load_n=0.2 * GRAVITY,
    )


def estimate(name: ParameterName, mean: float, std: float, bounds: tuple[float, float]) -> ParameterEstimate:
    return ParameterEstimate(
        name=name,
        mean=mean,
        std=std,
        interval95=(mean - 2 * std, mean + 2 * std),
        physical_bounds=bounds,
        units="kg" if name == ParameterName.MASS else "1",
        status="identified",
        method="fixture",
    )


def test_mass_estimator_recovers_quasistatic_load_and_tare() -> None:
    evidence = ExperimentEvidence(
        evidence_id="weigh-1",
        experiment="weigh",
        samples=tuple(sample(i * 0.1, force_z=1.5 + 0.2 * GRAVITY) for i in range(10)),
        metadata={"tare_force_z_n": 1.5},
    )
    result = estimate_mass(evidence)
    assert result.mean == pytest_approx(0.2, abs=1e-8)
    assert result.status == "identified"


def test_static_friction_brackets_breakaway_from_measured_force() -> None:
    forces = [0.5, 0.8, 1.0, 1.15, 1.2]
    evidence = ExperimentEvidence(
        evidence_id="static-1",
        experiment="static_friction",
        samples=tuple(sample(i * 0.1, force_x=f, vx=0.02 if i >= 4 else 0.0) for i, f in enumerate(forces)),
    )
    result = estimate_static_friction(evidence, estimate(ParameterName.MASS, 0.2, 0.001, (0.08, 0.45)))
    expected = forces[-1] / (0.2 * GRAVITY)
    assert result.interval95[0] <= expected <= result.interval95[1]


def test_dynamic_friction_remains_distinct_from_static() -> None:
    mu_dynamic = 0.31
    mass = estimate(ParameterName.MASS, 0.2, 0.001, (0.08, 0.45))
    static = estimate(ParameterName.STATIC_FRICTION, 0.62, 0.01, (0.2, 0.9))
    force = mu_dynamic * mass.mean * GRAVITY
    evidence = ExperimentEvidence(
        evidence_id="dynamic-1",
        experiment="dynamic_friction",
        samples=tuple(sample(i * 0.05, force_x=force, vx=0.04) for i in range(20)),
    )
    result = estimate_dynamic_friction(evidence, mass, static)
    assert result.mean == pytest_approx(mu_dynamic, abs=0.01)
    assert result.mean < static.mean


def test_black_box_surrogate_calibration_reports_holdout_error() -> None:
    mass = 0.2
    mu = 0.3
    dt = 0.05
    force = 0.8
    velocity = 0.03
    position = 0.0
    samples = []
    for index in range(40):
        samples.append(
            MotionSample(
                timestamp_s=index * dt,
                position_m=(position, 0.0, 0.025),
                velocity_m_s=(velocity, 0.0, 0.0),
                measured_force_n=(force, 0.0, 0.0),
                normal_load_n=mass * GRAVITY,
            )
        )
        velocity += (force - mu * mass * GRAVITY) / mass * dt
        position += velocity * dt
    evidence = ExperimentEvidence(
        evidence_id="calibration-1", experiment="dynamic_friction", samples=tuple(samples)
    )
    result = calibrate_coulomb_surrogate(evidence, mass, initial_mu=0.4)
    assert result["success"]
    assert result["effective_dynamic_friction"] == pytest_approx(mu, abs=0.02)
    assert "validation_position_rmse_m" in result


def pytest_approx(value: float, **kwargs: float):
    import pytest

    return pytest.approx(value, **kwargs)
