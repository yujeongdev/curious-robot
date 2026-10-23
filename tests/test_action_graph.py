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
HASH_C = "sha256:" + "c" * 64


def _compiled_graph() -> ActionGraph:
    return compile_motion_action_graph(
        task_id="push-cube-v1",
        task_version="1.0.0",
        object_id="cube",
        object_archetype_version="1.0.0",
        geometry_hash=HASH_C,
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


def test_graph_identity_ignores_parameter_key_order() -> None:
    graph = _compiled_graph()
    document = graph.model_dump()
    motion_index = next(
        index
        for index, node in enumerate(document["nodes"])
        if node["kind"] == ActionNodeKind.MOTION
    )
    parameters = document["nodes"][motion_index]["parameters"]
    document["nodes"][motion_index]["parameters"] = dict(reversed(tuple(parameters.items())))

    reordered = ActionGraph.model_validate(document)

    assert reordered.graph_id == graph.graph_id


def test_compiler_rejects_deadline_shorter_than_success_path() -> None:
    with pytest.raises(ValueError, match="shorter than its success path"):
        compile_motion_action_graph(
            task_id="push-cube-v1",
            task_version="1.0.0",
            object_id="cube",
            object_archetype_version="1.0.0",
            geometry_hash=HASH_C,
            calibration_id=HASH_A,
            protocol_binding_id=HASH_B,
            phases=("approach", "push", "retreat"),
            phase_durations_s=(0.5, 1.0, 0.5),
            contact_expectations=("unexpected", "maintain", "release"),
            observation_timeout_s=0.25,
            wall_clock_deadline_s=3.0,
        )


def test_deserialized_graph_revalidates_longest_path_deadline() -> None:
    graph = _compiled_graph()

    with pytest.raises(ValidationError, match="shorter than its longest path"):
        ActionGraph.model_validate(
            {**graph.model_dump(), "wall_clock_deadline_s": 1.0}
        )


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
    edges = tuple(
        edge
        for edge in graph.edges
        if not (
            edge.source_node_id == "observe_terminal_object_pose"
            and edge.guard is EdgeGuard.ON_SUCCESS
        )
    ) + (
        ActionGraphEdge(
            source_node_id="observe_terminal_object_pose",
            target_node_id="observe_initial_object_pose",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        ActionGraphEdge(
            source_node_id="observe_initial_object_pose",
            target_node_id="verify_goal",
            guard=EdgeGuard.ON_SAFETY_TRIP,
        ),
    )

    with pytest.raises(ValidationError, match="must be acyclic"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})


def test_graph_rejects_motion_failure_that_bypasses_safe_stop() -> None:
    graph = _compiled_graph()
    motion = next(node for node in graph.nodes if node.kind is ActionNodeKind.MOTION)
    edges = tuple(
        edge
        for edge in graph.edges
        if not (edge.source_node_id == motion.node_id and edge.guard is EdgeGuard.ON_FAILURE)
    ) + (
        ActionGraphEdge(
            source_node_id=motion.node_id,
            target_node_id="terminal_completed",
            guard=EdgeGuard.ON_FAILURE,
        ),
    )

    with pytest.raises(ValidationError, match="must reach safe-stop"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})


def test_graph_rejects_observation_failure_that_bypasses_safe_stop() -> None:
    graph = _compiled_graph()
    node_id = "observe_initial_object_pose"
    edges = tuple(
        edge
        for edge in graph.edges
        if not (edge.source_node_id == node_id and edge.guard is EdgeGuard.ON_FAILURE)
    ) + (
        ActionGraphEdge(
            source_node_id=node_id,
            target_node_id="terminal_completed",
            guard=EdgeGuard.ON_FAILURE,
        ),
    )

    with pytest.raises(ValidationError, match="must reach safe-stop"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})


def test_graph_rejects_safe_stop_omitted_from_terminal_declarations() -> None:
    graph = _compiled_graph()
    terminals = tuple(
        node_id for node_id in graph.terminal_node_ids if node_id != "terminal_safe_stop"
    )

    with pytest.raises(ValidationError, match="terminal-kind node"):
        ActionGraph.model_validate(
            {**graph.model_dump(), "terminal_node_ids": terminals}
        )


def test_graph_rejects_regular_terminal_omitted_from_declarations() -> None:
    graph = _compiled_graph()
    terminals = tuple(
        node_id
        for node_id in graph.terminal_node_ids
        if node_id != "terminal_return_to_agent"
    )

    with pytest.raises(ValidationError, match="terminal-kind node"):
        ActionGraph.model_validate(
            {**graph.model_dump(), "terminal_node_ids": terminals}
        )


def test_graph_rejects_operational_node_without_success_transition() -> None:
    graph = _compiled_graph()
    node_id = "observe_initial_object_pose"
    edges = tuple(
        edge
        for edge in graph.edges
        if not (edge.source_node_id == node_id and edge.guard is EdgeGuard.ON_SUCCESS)
    ) + (
        ActionGraphEdge(
            source_node_id=node_id,
            target_node_id="validate_initial_observation",
            guard=EdgeGuard.ON_GOAL_MISS,
        ),
    )

    with pytest.raises(ValidationError, match="requires a success edge"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})


def test_graph_rejects_verification_without_return_to_agent_goal_miss() -> None:
    graph = _compiled_graph()
    edges = tuple(
        edge
        for edge in graph.edges
        if not (
            edge.source_node_id == "verify_goal"
            and edge.guard is EdgeGuard.ON_GOAL_MISS
        )
    )

    with pytest.raises(ValidationError, match="requires a goal-miss edge"):
        ActionGraph.model_validate({**graph.model_dump(), "edges": edges})
