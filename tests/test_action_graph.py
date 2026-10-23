import pytest
from pydantic import ValidationError

from curious_robot.action_graph import (
    ActionGraph,
    ActionGraphEdge,
    ActionGraphNode,
    ActionNodeKind,
    EdgeGuard,
    MotionControlMode,
    NodeAuthority,
    NodeExecutor,
    NodeOutcome,
    compile_motion_action_graph,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _compiled_graph() -> ActionGraph:
    return compile_motion_action_graph(
        object_id="cube",
        calibration_id=HASH_A,
        protocol_binding_id=HASH_B,
        phases=("approach", "contact_push", "retreat"),
        phase_durations_s=(0.5, 1.0, 0.5),
        contact_expectations=("unexpected", "maintain", "release"),
        observation_timeout_s=0.25,
        wall_clock_deadline_s=5.0,
    )


def test_motion_graph_is_content_addressed_and_routes_every_failure_to_safe_stop() -> None:
    graph = _compiled_graph()
    clone = ActionGraph.model_validate_json(graph.model_dump_json())
    motion_nodes = tuple(node for node in graph.nodes if node.kind is ActionNodeKind.MOTION)

    assert graph.graph_id == clone.graph_id
    assert graph.entry_node_id == "observe_initial_object_pose"
    assert {node.control_mode for node in motion_nodes} == {MotionControlMode.CARTESIAN_POSE}
    assert graph.next_node(graph.entry_node_id, NodeOutcome.SUCCESS) == (
        "validate_initial_observation"
    )
    assert graph.next_node("validate_initial_observation", NodeOutcome.SUCCESS) == (
        motion_nodes[0].node_id
    )
    assert graph.next_node(motion_nodes[0].node_id, NodeOutcome.SUCCESS) == motion_nodes[1].node_id
    for node in motion_nodes:
        assert graph.next_node(node.node_id, NodeOutcome.FAILURE) == "terminal_safe_stop"
        assert graph.next_node(node.node_id, NodeOutcome.SAFETY_TRIP) == "terminal_safe_stop"
    assert graph.next_node("terminal_completed", NodeOutcome.SUCCESS) is None
    assert graph.next_node("verify_goal", NodeOutcome.GOAL_MISS) == "terminal_return_to_agent"


def test_graph_identity_changes_with_bound_protocol() -> None:
    first = _compiled_graph()
    second = first.model_copy(update={"protocol_binding_id": HASH_A})

    assert first.graph_id != second.graph_id


def test_graph_rejects_unreachable_nodes_and_ambiguous_edges() -> None:
    graph = _compiled_graph()
    unreachable = ActionGraphNode(
        node_id="orphan",
        kind=ActionNodeKind.TERMINAL,
        authority=NodeAuthority.SUPERVISOR,
        executor=NodeExecutor.SUPERVISOR,
        timeout_s=0.1,
    )
    with pytest.raises(ValidationError, match="unreachable"):
        ActionGraph.model_validate(
            {
                **graph.model_dump(),
                "nodes": (*graph.nodes, unreachable),
                "terminal_node_ids": (*graph.terminal_node_ids, unreachable.node_id),
                "maximum_node_executions": graph.maximum_node_executions + 1,
            }
        )

    duplicate = ActionGraphEdge(
        source_node_id=graph.entry_node_id,
        target_node_id="terminal_safe_stop",
        guard=EdgeGuard.ON_FAILURE,
    )
    with pytest.raises(ValidationError, match="ambiguous"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": (*graph.edges, duplicate)})


def test_graph_rejects_unbounded_cycles() -> None:
    graph = _compiled_graph()
    motion_nodes = tuple(node for node in graph.nodes if node.kind is ActionNodeKind.MOTION)
    edges = tuple(
        edge
        for edge in graph.edges
        if not (
            edge.source_node_id == motion_nodes[1].node_id
            and edge.guard is EdgeGuard.ON_FAILURE
        )
    ) + (
        ActionGraphEdge(
            source_node_id=motion_nodes[1].node_id,
            target_node_id=motion_nodes[0].node_id,
            guard=EdgeGuard.ON_FAILURE,
        ),
    )

    with pytest.raises(ValidationError, match="cycles require"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})
