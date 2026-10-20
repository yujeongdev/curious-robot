from __future__ import annotations

import inspect
from dataclasses import fields
from types import SimpleNamespace
from uuid import UUID

from curious_robot.belief import initial_belief
from curious_robot.environment import (
    BeliefEnvironment,
    NewtonWorld,
    WorldConfig,
    physical_belief_snapshot_id,
    rollout_candidate,
)
from curious_robot.protocol import load_default_protocol, protocol_sha256
from curious_robot.schemas import ActionCandidate, MotionSample, ObjectPoseObservation, Pose, PredictedOutcome
from curious_robot.tools import RobotTools


def _mutated_config() -> WorldConfig:
    values = {
        "device": "cpu",
        "dt_s": 0.003,
        "initial_position_m": (-0.091, 0.017, 0.026),
        "target_position_m": (0.233, -0.021, 0.027),
        "target_radius_m": 0.041,
        "observation_noise_std": 0.0017,
        "force_noise_std": 0.027,
        "include_robot": True,
        "seed": 831,
        "arm_position_stiffness": 321.0,
        "arm_position_damping": 37.0,
        "gripper_position_stiffness": 144.0,
        "gripper_position_damping": 13.0,
        "pusher_radius_m": 0.009,
        "pusher_density_kg_m3": 212.0,
        "pusher_friction": 0.73,
        "maximum_tool_force_n": 11.5,
        "contact_sensor_cutoff_hz": 7.5,
        "prediction_residual_tolerance_m": 0.024,
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
            return [
                MotionSample(
                    timestamp_s=0.1,
                    position_m=config.target_position_m,
                    velocity_m_s=(0.0, 0.0, 0.0),
                    measured_force_n=(1.0, 0.0, 0.0),
                )
            ]

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
    assert captured["push"] == (candidate.target_m, candidate.max_force_n, custom_final_push)
    assert captured["closed"] is True
    assert outcome.world_instance_id == UUID(FakeWorld.world_instance_id)
    assert outcome.world_config_id == config.config_id
    assert outcome.experiment_protocol_id == f"physical-experiment:{protocol_sha256(protocol)}"
    assert outcome.belief_revision == belief.revision
    assert outcome.belief_snapshot_id == physical_belief_snapshot_id(belief)


def test_robot_tools_passes_run_protocol_and_only_updates_observed_start(monkeypatch) -> None:
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
    )

    tools.simulate_candidate_action(candidate)

    supplied_config = captured["config"]
    assert isinstance(supplied_config, WorldConfig)
    assert supplied_config.initial_position_m == observed_start
    for field in fields(WorldConfig):
        if field.name != "initial_position_m":
            assert getattr(supplied_config, field.name) == getattr(config, field.name)
    assert captured["protocol"] is protocol


def test_execute_push_controller_does_not_use_exact_cube_state_for_decisions() -> None:
    source = inspect.getsource(NewtonWorld.execute_push)
    assert "observe_object_pose" in source
    assert "cube_controller_state" not in source
