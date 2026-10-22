from __future__ import annotations

import inspect
import math
from dataclasses import fields
from types import SimpleNamespace
from uuid import UUID

from curious_robot.belief import initial_belief
from curious_robot.control import NewtonFrankaController, PushExecutionOutcome, PushExecutionResult
from curious_robot.environment import (
    BeliefEnvironment,
    WorldConfig,
    physical_belief_snapshot_id,
    rollout_candidate,
    rollout_candidate_in_world,
)
from curious_robot.protocol import configure_push_protocol, load_default_protocol, protocol_sha256
from curious_robot.schemas import ActionCandidate, MotionSample, ObjectPoseObservation, Pose, PredictedOutcome
from curious_robot.tools import RobotTools


def _mutated_config() -> WorldConfig:
    values = {
        "task_id": "push-t-bar-v1",
        "device": "cpu",
        "dt_s": 0.003,
        "initial_position_m": (-0.091, 0.017, 0.026),
        "initial_yaw_rad": 0.07,
        "target_position_m": (0.233, -0.021, 0.027),
        "target_radius_m": 0.041,
        "target_yaw_rad": 0.12,
        "target_yaw_tolerance_rad": 0.08,
        "observation_noise_std": 0.0017,
        "force_noise_std": 0.027,
        "include_robot": True,
        "object_kind": "t_bar",
        "object_archetype_version": "1.0.0",
        "seed": 831,
        "arm_position_stiffness": 321.0,
        "arm_position_damping": 37.0,
        "contact_arm_position_stiffness": 233.0,
        "contact_arm_position_damping": 29.0,
        "gripper_position_stiffness": 144.0,
        "gripper_position_damping": 13.0,
        "tool_tip_radius_m": 0.01,
        "tool_tip_density_kg_m3": 180.0,
        "tool_tip_friction": 0.61,
        "maximum_tool_force_n": 11.5,
        "maximum_cartesian_speed_m_s": 0.13,
        "contact_sensor_cutoff_hz": 7.5,
        "prediction_residual_tolerance_m": 0.024,
        "reexecution_yaw_tolerance_rad": 0.009,
        "maximum_joint_target_speed_rad_s": 1.23,
        "ik_position_tolerance_m": 0.006,
        "cartesian_tracking_tolerance_m": 0.011,
        "maximum_cartesian_settle_s": 0.91,
        "maximum_cartesian_corrections": 3,
        "maximum_cartesian_correction_m": 0.031,
    }
    assert set(values) == {item.name for item in fields(WorldConfig)}
    return WorldConfig(**values)


def test_rollout_preserves_complete_config_protocol_and_posterior_provenance(monkeypatch) -> None:
    config = _mutated_config()
    default_protocol = load_default_protocol()
    custom_final_push = default_protocol.final_push.model_copy(
        update={"maximum_attempts": 7, "command_increment_m": 0.00073}
    )
    protocol = default_protocol.model_copy(
        update={"schema_version": "review-test", "final_push": custom_final_push}
    )
    belief = initial_belief()
    candidate = ActionCandidate(
        action_id="mutated-config-push",
        kind="push",
        target_m=config.target_position_m,
        speed_m_s=0.04,
        max_force_n=8.0,
        push_height_m=0.02,
        contact_surface_id="stem_rear_x",
    )
    captured: dict[str, object] = {}

    class FakeWorld:
        world_instance_id = "c8af82d6-65bd-4b35-b98f-4d9f0f17138e"

        def __init__(self, effective_config: WorldConfig) -> None:
            self.world_config_id = effective_config.config_id

        def settle(self) -> None:
            captured["settled"] = True

        def execute_push(self, target_m, max_force_n, *, protocol):  # noqa: ANN001, ANN201
            captured["push"] = (target_m, max_force_n, protocol)
            return PushExecutionResult(
                samples=(
                    MotionSample(
                        timestamp_s=0.1,
                        position_m=config.target_position_m,
                        velocity_m_s=(0.0, 0.0, 0.0),
                        measured_force_n=(1.0, 0.0, 0.0),
                    ),
                ),
                outcome=PushExecutionOutcome.COMPLETED,
                peak_contact_force_n=1.0,
            )

        def observe_object_pose(self) -> ObjectPoseObservation:
            return ObjectPoseObservation(
                pose=Pose(position_m=config.target_position_m),
                linear_velocity_m_s=(0.0, 0.0, 0.0),
                angular_velocity_rad_s=(0.0, 0.0, 0.0),
                covariance_diagonal=(0.0,) * 6,
                timestamp_s=0.1,
            )

        def close(self) -> None:
            captured["closed"] = True

    def fake_from_belief(cls, supplied_belief, supplied_config, **kwargs):  # noqa: ANN001, ANN202
        captured["belief"] = supplied_belief
        captured["config"] = supplied_config
        captured["world_kwargs"] = kwargs
        return FakeWorld(supplied_config)

    monkeypatch.setattr(BeliefEnvironment, "from_belief", classmethod(fake_from_belief))

    outcome = rollout_candidate(belief, candidate, config, protocol)

    assert captured["config"] is config
    assert captured["belief"] is belief
    effective_push = captured["push"]
    assert effective_push[:2] == (candidate.target_m, candidate.max_force_n)
    assert effective_push[2] == configure_push_protocol(
        custom_final_push,
        dt_s=config.dt_s,
        speed_m_s=candidate.speed_m_s,
        push_height_m=candidate.push_height_m,
        contact_surface_id=candidate.contact_surface_id,
        safety_stop_fraction=1.0,
    )
    assert captured["closed"] is True
    assert outcome.world_instance_id == UUID(FakeWorld.world_instance_id)
    assert outcome.world_config_id == config.config_id
    assert outcome.experiment_protocol_id == f"physical-experiment:{protocol_sha256(protocol)}"
    assert outcome.belief_revision == belief.revision
    assert outcome.belief_snapshot_id == physical_belief_snapshot_id(belief)


def test_robot_tools_uses_recorded_fresh_deployment_start(monkeypatch) -> None:
    config = _mutated_config()
    protocol = load_default_protocol().model_copy(update={"schema_version": "exact-run-protocol"})
    observed_start = (-0.043, 0.019, 0.025)
    reality = SimpleNamespace(
        sim_time_s=0.0,
        observe_object_pose=lambda: SimpleNamespace(pose=Pose(position_m=observed_start)),
    )
    captured: dict[str, object] = {}

    def fake_rollout(belief, candidate, supplied_config, supplied_protocol, **kwargs):  # noqa: ANN001, ANN202
        captured.update(
            belief=belief,
            candidate=candidate,
            config=supplied_config,
            protocol=supplied_protocol,
            kwargs=kwargs,
        )
        return PredictedOutcome(
            action_id=candidate.action_id,
            final_position_m=observed_start,
            target_error_m=0.0,
            success_probability=1.0,
            tipping_margin=1.0,
            peak_force_n=0.0,
            duration_s=0.0,
            feasible=True,
            rationale="test",
        )

    monkeypatch.setattr("curious_robot.tools.rollout_candidate", fake_rollout)
    tools = RobotTools(reality, initial_belief(), config, protocol=protocol)
    candidate = ActionCandidate(
        action_id="tool-flow",
        kind="push",
        target_m=config.target_position_m,
        speed_m_s=0.04,
        max_force_n=8.0,
        contact_surface_id="stem_rear_x",
    )

    tools.simulate_candidate_action(candidate)

    supplied_config = captured["config"]
    assert isinstance(supplied_config, WorldConfig)
    assert observed_start != config.initial_position_m
    assert supplied_config.initial_position_m == config.initial_position_m
    for field in fields(WorldConfig):
        if field.name != "initial_position_m":
            assert getattr(supplied_config, field.name) == getattr(config, field.name)
    supplied_protocol = captured["protocol"]
    assert isinstance(supplied_protocol, type(protocol))
    assert supplied_protocol.schema_version == protocol.schema_version
    assert {
        supplied_protocol.static_probe.contact_surface_id,
        supplied_protocol.dynamic_probe.contact_surface_id,
        supplied_protocol.final_push.contact_surface_id,
    } == {"stem_rear_x"}


def test_execute_push_controller_does_not_use_exact_cube_state_for_decisions() -> None:
    source = inspect.getsource(NewtonFrankaController.execute_push)
    assert "observe_object_pose" in source
    assert "cube_controller_state" not in source


def test_tbar_rollout_that_misses_yaw_goal_is_not_feasible() -> None:
    config = WorldConfig(
        task_id="push-t-bar-v1",
        device="cpu",
        include_robot=True,
        object_kind="t_bar",
        initial_position_m=(-0.12, 0.0, 0.02),
        target_position_m=(0.15, 0.0, 0.02),
        target_radius_m=0.03,
        target_yaw_rad=0.0,
        target_yaw_tolerance_rad=math.radians(9.0),
    )
    half_yaw = math.radians(10.0) / 2.0
    world = SimpleNamespace(
        world_instance_id="f2019018-07a6-4bbb-82dc-51cc5403983a",
        world_config_id=config.config_id,
        settle=lambda: None,
        execute_push=lambda *_args, **_kwargs: PushExecutionResult(
            samples=(),
            outcome=PushExecutionOutcome.COMPLETED,
            peak_contact_force_n=0.0,
        ),
        observe_object_pose=lambda: ObjectPoseObservation(
            pose=Pose(
                position_m=config.target_position_m,
                orientation_xyzw=(0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)),
            ),
            linear_velocity_m_s=(0.0, 0.0, 0.0),
            angular_velocity_rad_s=(0.0, 0.0, 0.0),
            covariance_diagonal=(0.0,) * 6,
            timestamp_s=0.0,
        ),
    )
    candidate = ActionCandidate(
        action_id="yaw-miss",
        kind="push",
        target_m=config.target_position_m,
        speed_m_s=0.04,
        max_force_n=8.0,
        push_height_m=0.012,
        contact_surface_id="stem_rear_x",
    )

    outcome = rollout_candidate_in_world(
        world,
        initial_belief(),
        candidate,
        config,
        load_default_protocol(),
    )

    assert outcome.target_error_m == 0.0
    assert math.isclose(outcome.target_yaw_error_rad or 0.0, math.radians(10.0))
    assert outcome.feasible is False
