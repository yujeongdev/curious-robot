import numpy as np
import pytest

from curious_robot.belief import initial_belief
from curious_robot.environment import PhysicalParameters, TruthEnvironment, WorldConfig
from curious_robot.ports import RobotRealityAdapter
from curious_robot.schemas import ParameterName
from curious_robot.sysid import estimate_static_friction
from curious_robot.tools import RobotTools


@pytest.fixture
def tools() -> RobotTools:
    config = WorldConfig(
        device="cuda:0",
        include_robot=True,
        seed=11,
        force_noise_std=0.001,
        observation_noise_std=0.0,
    )
    world = TruthEnvironment(PhysicalParameters(0.22, 0.58, 0.34), config)
    instance = RobotTools(RobotRealityAdapter(world), initial_belief(), config)
    yield instance
    world.close()


@pytest.mark.gpu
def test_newton_experiments_identify_hidden_parameters_without_truth_api(tools: RobotTools) -> None:
    prior_mass = tools.belief.parameters[ParameterName.MASS]
    evidence = tools.probe_static_friction()
    static = estimate_static_friction(evidence, prior_mass)
    assert static.interval95[0] <= 0.58 <= static.interval95[1]
    assert any(abs(sample.measured_force_n[0]) > 0.05 for sample in evidence.samples)
    assert any(sample.contact_valid for sample in evidence.samples)
    assert all(
        sample.contact_pair == ("fr3_pusher_tip", "unknown_cube_collision")
        for sample in evidence.samples
        if sample.contact_valid
    )
    assert evidence.metadata["protocol_sha256"]
    assert evidence.metadata["experiment_correlation_id"] == evidence.evidence_id
    assert evidence.metadata["accepted_contact_sample_count"] == sum(
        sample.contact_valid for sample in evidence.samples
    )
    assert evidence.samples[-1].position_m[0] - evidence.samples[0].position_m[0] > 0.002
    assert "SensorContact" in evidence.metadata["producer"]
    assert all("truth" not in record.model_dump_json() for record in tools.records)
    assert all(
        sample.measured_normal_load_n is None for evidence in tools.evidence for sample in evidence.samples
    )
    pose_before = tools.observe_object_pose().pose.position_m
    tools.return_object_home()
    pose_after = tools.observe_object_pose().pose.position_m
    assert pose_after == pytest.approx(pose_before, abs=1.0e-9)
    reset_record = next(record for record in reversed(tools.records) if record.tool == "return_object_home")
    assert reset_record.result_summary["restored"] is False
    assert "forbidden" in str(reset_record.result_summary["reason"])


@pytest.mark.gpu
def test_compliance_rejects_force_command_until_feedback_controller_is_validated(
    tools: RobotTools,
) -> None:
    tools.enable_cartesian_compliance()
    with pytest.raises(RuntimeError, match="desired-force control unavailable"):
        tools.set_desired_force((5.0, 0.0, 0.0))
    assert not any(record.tool == "set_desired_force" for record in tools.records)


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
def test_unvalidated_pick_place_is_rejected_instead_of_using_cube_force() -> None:
    config = WorldConfig(device="cuda:0", include_robot=False, seed=23, force_noise_std=0.0)
    worlds = [
        TruthEnvironment(PhysicalParameters(hidden_mass, 0.58, 0.34), config) for hidden_mass in (0.12, 0.41)
    ]
    try:
        for world in worlds:
            with pytest.raises(RuntimeError, match="bilateral physical grasp"):
                world.execute_pick_place(
                    config.target_position_m,
                    assumed_mass_kg=0.2,
                )
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


@pytest.mark.gpu
def test_native_contact_sensor_is_zero_before_contact_and_nonzero_during_robot_push() -> None:
    config = WorldConfig(device="cuda:0", include_robot=True, seed=31, force_noise_std=0.0)
    world = TruthEnvironment(PhysicalParameters(0.2, 0.58, 0.34), config)
    try:
        world.settle(0.05)
        assert np.linalg.norm(world.last_tool_contact_force_n) < 0.05
        tools = RobotTools(RobotRealityAdapter(world), initial_belief(), config)
        evidence = tools.probe_static_friction()
        assert max(abs(sample.measured_force_n[0]) for sample in evidence.samples) > 0.1
        assert any(np.linalg.norm(sample.measured_force_n) > 0.1 for sample in evidence.samples)
        assert any(sample.contact_valid for sample in evidence.samples)
    finally:
        world.close()


@pytest.mark.gpu
def test_visual_contact_requires_raw_native_contact_and_keeps_normal_distinct_from_force() -> None:
    class EventSink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def emit(self, intent: dict) -> None:
            self.events.append(intent)

    sink = EventSink()
    config = WorldConfig(device="cuda:0", include_robot=True, seed=37, force_noise_std=0.0)
    world = TruthEnvironment(
        PhysicalParameters(0.2, 0.58, 0.34),
        config,
        event_sink=sink,
    )
    try:
        cube_position = np.asarray(world.observe_object_pose().pose.position_m)

        # A low-pass residual is telemetry, not evidence of current contact geometry.
        world.last_tool_contact_force_n = np.array((-0.4, 0.0, 0.0))
        world.last_raw_tool_contact_force_n[:] = 0.0
        world.last_tool_contact_position_m = np.array((0.1, 0.2, 0.3))
        world.last_tool_contact_normal_world = np.array((0.0, 1.0, 0.0))
        world.last_tool_contact_active = False
        world._emit_visual_frame(cube_position)
        assert sink.events[-1]["payload"]["contacts"] == ()
        assert sink.events[-1]["payload"]["vectors"] == ()

        # Native normal and native force remain separate physical quantities.
        world.last_raw_tool_contact_force_n = np.array((-2.0, 0.0, 0.0))
        world.last_tool_contact_normal_world = np.array((0.0, 1.0, 0.0))
        world.last_tool_contact_active = True
        world._emit_visual_frame(cube_position)
        contact = sink.events[-1]["payload"]["contacts"][0]
        vector = sink.events[-1]["payload"]["vectors"][0]
        assert contact["normal_world"] == (0.0, 1.0, 0.0)
        assert contact["force_world_n"] == (2.0, -0.0, -0.0)
        assert contact["shape_a_label"] == "fr3_pusher_tip"
        assert contact["shape_b_label"] == "unknown_cube_collision"
        assert "filtered" in vector["label"]
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
