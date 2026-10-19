import numpy as np
import pytest

from curious_robot.belief import initial_belief
from curious_robot.environment import PhysicalParameters, TruthEnvironment, WorldConfig
from curious_robot.sysid import estimate_dynamic_friction, estimate_mass, estimate_static_friction
from curious_robot.tools import RobotTools


@pytest.fixture
def tools() -> RobotTools:
    config = WorldConfig(
        device="cuda:0",
        include_robot=False,
        seed=11,
        force_noise_std=0.001,
        observation_noise_std=0.0,
    )
    world = TruthEnvironment(PhysicalParameters(0.22, 0.58, 0.34), config)
    instance = RobotTools(world, initial_belief(), config)
    yield instance
    world.close()


@pytest.mark.gpu
def test_newton_experiments_identify_hidden_parameters_without_truth_api(tools: RobotTools) -> None:
    mass = estimate_mass(tools.weigh_object())
    static = estimate_static_friction(tools.probe_static_friction(), mass)
    dynamic = estimate_dynamic_friction(tools.probe_dynamic_friction(), mass, static)
    assert mass.mean == pytest.approx(0.22, rel=0.05)
    assert static.interval95[0] <= 0.58 <= static.interval95[1]
    assert dynamic.mean == pytest.approx(0.34, abs=0.06)
    assert all("truth" not in record.model_dump_json() for record in tools.records)
    assert all(
        sample.measured_normal_load_n is None for evidence in tools.evidence for sample in evidence.samples
    )
    assert any(record.tool == "return_object_home" for record in tools.records)


@pytest.mark.gpu
def test_compliance_rejects_unbounded_force(tools: RobotTools) -> None:
    tools.enable_cartesian_compliance()
    with pytest.raises(ValueError, match="15 N"):
        tools.set_desired_force((20.0, 0.0, 0.0))


@pytest.mark.gpu
def test_motion_samples_do_not_expose_hidden_mass_as_normal_load() -> None:
    hidden_mass = 0.371234
    world = TruthEnvironment(
        PhysicalParameters(hidden_mass, 0.58, 0.34),
        WorldConfig(device="cuda:0", include_robot=False, seed=19, force_noise_std=0.0),
    )
    try:
        payload = world.step().model_dump()
        assert "normal_load_n" not in payload
        assert payload["measured_normal_load_n"] is None
        assert payload["normal_load_standard_deviation_n"] is None
        assert payload["normal_load_producer"] is None
        assert hidden_mass * 9.81 not in _numeric_leaves(payload)
    finally:
        world.close()


@pytest.mark.gpu
def test_pick_place_gravity_compensation_uses_assumed_mass() -> None:
    config = WorldConfig(device="cuda:0", include_robot=False, seed=23, force_noise_std=0.0)
    worlds = [
        TruthEnvironment(PhysicalParameters(hidden_mass, 0.58, 0.34), config) for hidden_mass in (0.12, 0.41)
    ]
    try:
        first_forces = [
            world.execute_pick_place(
                config.target_position_m,
                assumed_mass_kg=0.2,
            )[0].measured_force_n
            for world in worlds
        ]
        assert np.asarray(first_forces[0]) == pytest.approx(first_forces[1], abs=1e-12)
    finally:
        for world in worlds:
            world.close()


@pytest.mark.gpu
def test_franka_cartesian_command_moves_articulation() -> None:
    config = WorldConfig(device="cuda:0", include_robot=True, seed=5)
    world = TruthEnvironment(PhysicalParameters(0.2, 0.5, 0.3), config)
    try:
        before = world.state_0.joint_q.numpy()[:7].copy()
        world.command_cartesian((-0.10, 0.0, 0.15), duration_s=0.15)
        after = world.state_0.joint_q.numpy()[:7]
        assert abs(after - before).max() > 0.05
    finally:
        world.close()


def _numeric_leaves(value: object) -> list[float]:
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in _numeric_leaves(child)]
    if isinstance(value, (list, tuple)):
        return [leaf for child in value for leaf in _numeric_leaves(child)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    return []
