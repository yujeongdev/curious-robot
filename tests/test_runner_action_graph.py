from curious_robot.action_graph import NodeOutcome
from curious_robot.planning import candidate_actions
from curious_robot.protocol import load_default_protocol
from curious_robot.runner import (
    MAX_FRANKA_JOINT_DELTA_RAD,
    _EpisodeEvents,
    _skill_timeout_bound_s,
)
from curious_robot.tasks import get_task_preset
from curious_robot.trajectory import minimum_jerk_duration_s
from curious_robot.world_config import WorldConfig


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, intent: dict[str, object]) -> None:
        self.events.append(intent)


def test_action_graph_failure_closes_with_absorbing_safe_stop() -> None:
    sink = _RecordingSink()
    events = _EpisodeEvents(sink)
    sink.events.clear()

    events.stop_action_graph(
        "sha256:" + "a" * 64,
        "execute_selected_skill",
        "failed",
        NodeOutcome.FAILURE,
    )

    assert [event["type"] for event in sink.events] == [
        "action.node_transitioned",
        "action.node_transitioned",
        "action.node_transitioned",
    ]
    assert [event["payload"]["node_id"] for event in sink.events] == [
        "execute_selected_skill",
        "terminal_safe_stop",
        "terminal_safe_stop",
    ]
    assert [event["payload"]["state"] for event in sink.events] == [
        "failed",
        "entered",
        "completed",
    ]


def test_skill_timeout_bounds_all_tbar_translation_and_yaw_attempts() -> None:
    task = get_task_preset("push-t-bar-v1")
    config = WorldConfig(task_id=task.preset_id)
    base_protocol = load_default_protocol()
    overrides = task.experiment_protocol_overrides.final_push
    protocol = base_protocol.model_copy(
        update={
            "final_push": base_protocol.final_push.model_copy(
                update=overrides.model_dump(exclude_none=True)
            )
        }
    )
    action = next(
        candidate
        for candidate in candidate_actions(config.target_position_m, task_id=task.preset_id)
        if candidate.kind == "push"
    )

    timeout_s = _skill_timeout_bound_s(action, task, protocol, config)
    translation_lower_bound_s = (
        protocol.final_push.maximum_attempts
        * protocol.final_push.maximum_travel_m
        / action.speed_m_s
    )
    yaw_lower_bound_s = (
        task.yaw_alignment.maximum_attempts
        * task.yaw_alignment.correction_travel_m
        / task.yaw_alignment.speed_m_s
    )
    cartesian_correction_bound_s = (
        (
            protocol.final_push.maximum_attempts
            + task.yaw_alignment.maximum_attempts
        )
        * 4
        * (config.maximum_cartesian_corrections + 1)
        * minimum_jerk_duration_s(
            MAX_FRANKA_JOINT_DELTA_RAD,
            config.maximum_joint_target_speed_rad_s,
        )
    )

    assert timeout_s > translation_lower_bound_s + yaw_lower_bound_s
    assert timeout_s > cartesian_correction_bound_s
