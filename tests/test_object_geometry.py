import numpy as np
import pytest

from curious_robot.belief import initial_belief
from curious_robot.environment import TruthEnvironment
from curious_robot.objects import T_BAR_V1
from curious_robot.ports import RobotRealityAdapter
from curious_robot.tools import RobotTools
from curious_robot.world_config import (
    PhysicalParameters,
    WorldConfig,
    compound_box_inertia,
    resolved_object_geometry,
)


def test_t_bar_matches_public_catalog_and_uses_non_overlapping_compound_geometry() -> None:
    geometry = resolved_object_geometry(WorldConfig(task_id="push-t-bar-v1", object_kind="t_bar"))

    assert geometry.body_label == "unknown_t_bar"
    assert [part.label for part in geometry.parts] == [
        "unknown_t_bar_stem_collision",
        "unknown_t_bar_crossbar_collision",
    ]
    assert tuple(part.center_m for part in geometry.parts) == tuple(
        part.center_object_m for part in T_BAR_V1.parts
    )
    assert tuple(part.size_m for part in geometry.parts) == tuple(
        tuple(2.0 * value for value in part.half_extents_m) for part in T_BAR_V1.parts
    )

    stem, crossbar = geometry.parts
    stem_max_x = stem.center_m[0] + stem.size_m[0] / 2.0
    crossbar_min_x = crossbar.center_m[0] - crossbar.size_m[0] / 2.0
    assert stem_max_x == pytest.approx(crossbar_min_x, abs=1.0e-12)


def test_cube_inertia_matches_closed_form_and_com_offset_uses_parallel_axis() -> None:
    geometry = resolved_object_geometry(WorldConfig(object_kind="cube"))
    mass_kg = 0.24
    centered = np.asarray(compound_box_inertia(geometry, mass_kg, (0.0, 0.0, 0.0)))
    expected_diagonal = mass_kg * 0.05**2 / 6.0
    assert centered == pytest.approx(np.eye(3) * expected_diagonal, abs=1.0e-12)

    shifted = np.asarray(compound_box_inertia(geometry, mass_kg, (0.004, -0.002, 0.0)))
    assert np.linalg.eigvalsh(shifted).min() > 0.0
    assert shifted[0, 1] != 0.0
    assert shifted[2, 2] > centered[2, 2]


def test_public_geometry_changes_world_identity_without_hidden_parameters() -> None:
    cube = WorldConfig(object_kind="cube")
    t_bar = WorldConfig(task_id="push-t-bar-v1", object_kind="t_bar")

    assert cube.config_id != t_bar.config_id
    assert "mass" not in cube.config_id
    assert "friction" not in t_bar.config_id
    with pytest.raises(ValueError, match="requires object kind"):
        WorldConfig(task_id="push-t-bar-v1", object_kind="cube")


@pytest.mark.gpu
def test_newton_t_bar_uses_one_body_two_shapes_and_explicit_hidden_inertia() -> None:
    parameters = PhysicalParameters(0.25, 0.55, 0.32, com_m=(0.003, -0.002, 0.0))
    config = WorldConfig(
        device="cuda:0",
        include_robot=False,
        task_id="push-t-bar-v1",
        object_kind="t_bar",
        initial_position_m=(-0.12, 0.0, 0.02),
        target_position_m=(0.15, 0.0, 0.02),
        observation_noise_std=0.0,
    )
    world = TruthEnvironment(parameters, config)
    try:
        assert world.model.body_label == ["unknown_t_bar"]
        assert world.object_shape_indices == frozenset({0, 1})
        assert tuple(world.model.shape_label[:2]) == (
            "unknown_t_bar_stem_collision",
            "unknown_t_bar_crossbar_collision",
        )
        assert float(world.model.body_mass.numpy()[0]) == pytest.approx(parameters.mass_kg)
        assert world.model.body_com.numpy()[0] == pytest.approx(parameters.com_m)
        expected = compound_box_inertia(world.object_geometry, parameters.mass_kg, parameters.com_m)
        np.testing.assert_allclose(world.model.body_inertia.numpy()[0], expected, rtol=1.0e-5)
        world.settle(0.02)
        assert world.observe_object_pose().pose.position_m[2] == pytest.approx(0.02, abs=0.002)
    finally:
        world.close()


@pytest.mark.gpu
def test_t_bar_shapes_share_contact_policy_and_produce_native_sensor_evidence() -> None:
    config = WorldConfig(
        device="cuda:0",
        include_robot=True,
        task_id="push-t-bar-v1",
        object_kind="t_bar",
        initial_position_m=(-0.12, 0.0, 0.02),
        target_position_m=(0.15, 0.0, 0.02),
    )
    world = TruthEnvironment(PhysicalParameters(0.25, 0.55, 0.32), config)
    try:
        robot_shapes = range(min(world.object_shape_indices))
        arm_shapes = set(robot_shapes).difference(world._tool_shape_indices)
        for object_shape in world.object_shape_indices:
            assert all(
                not world.model.shape_collision_filter_pairs.contains_pair(tool_shape, object_shape)
                for tool_shape in world._tool_shape_indices
            )
            assert all(
                world.model.shape_collision_filter_pairs.contains_pair(arm_shape, object_shape)
                for arm_shape in arm_shapes
            )
        evidence = RobotTools(RobotRealityAdapter(world), initial_belief(), config).probe_static_friction()
        native_samples = [sample for sample in evidence.samples if sample.contact_valid]
        assert native_samples
        assert max(abs(sample.measured_force_n[0]) for sample in native_samples) > 0.1
        assert {sample.contact_pair[1] for sample in native_samples} <= {
            "unknown_t_bar_stem_collision",
            "unknown_t_bar_crossbar_collision",
        }
    finally:
        world.close()
