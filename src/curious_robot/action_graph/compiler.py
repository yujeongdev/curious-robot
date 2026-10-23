"""Compile bounded motion phases into an immutable deterministic ActionGraph."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from .contracts import (
    ActionGraph,
    ActionGraphEdge,
    ActionGraphNode,
    ActionNodeKind,
    EdgeGuard,
    MotionControlMode,
    NodeAuthority,
    NodeExecutor,
)

TERMINAL_TRANSITION_TIMEOUT_S = 0.001


@dataclass(frozen=True, slots=True)
class _SupervisorNodes:
    observe_initial: ActionGraphNode
    validate_observation: ActionGraphNode
    observe_final: ActionGraphNode
    verify_goal: ActionGraphNode
    complete: ActionGraphNode
    return_to_agent: ActionGraphNode
    safe_stop: ActionGraphNode

    @property
    def all(self) -> tuple[ActionGraphNode, ...]:
        return (
            self.observe_initial,
            self.validate_observation,
            self.observe_final,
            self.verify_goal,
            self.complete,
            self.return_to_agent,
            self.safe_stop,
        )


def push_protocol_binding_id(protocol_document: str) -> str:
    return f"sha256:{hashlib.sha256(protocol_document.encode()).hexdigest()}"


def compile_motion_action_graph(
    *,
    object_id: str,
    calibration_id: str,
    protocol_binding_id: str,
    phases: Sequence[str],
    phase_durations_s: Sequence[float],
    contact_expectations: Sequence[str],
    observation_timeout_s: float,
    wall_clock_deadline_s: float,
    observation_capabilities: tuple[str, ...] = ("external_object_pose",),
) -> ActionGraph:
    """Bind observation, Cartesian phases, verification, and safe-stop paths."""

    motion_nodes = _compile_motion_nodes(phases, phase_durations_s, contact_expectations)
    supervisor = _compile_supervisor_nodes(observation_timeout_s, observation_capabilities)
    edges = (
        *_entry_edges(supervisor, motion_nodes[0]),
        *_motion_edges(motion_nodes, supervisor),
        *_exit_edges(supervisor),
    )
    return ActionGraph(
        graph_name="franka-bounded-push",
        object_id=object_id,
        calibration_id=calibration_id,
        protocol_binding_id=protocol_binding_id,
        entry_node_id=supervisor.observe_initial.node_id,
        terminal_node_ids=(
            supervisor.complete.node_id,
            supervisor.return_to_agent.node_id,
            supervisor.safe_stop.node_id,
        ),
        maximum_node_executions=len(motion_nodes) + len(supervisor.all),
        wall_clock_deadline_s=wall_clock_deadline_s,
        nodes=(
            supervisor.observe_initial,
            supervisor.validate_observation,
            *motion_nodes,
            supervisor.observe_final,
            supervisor.verify_goal,
            supervisor.complete,
            supervisor.return_to_agent,
            supervisor.safe_stop,
        ),
        edges=edges,
    )


def _compile_motion_nodes(
    phases: Sequence[str],
    durations_s: Sequence[float],
    contact_expectations: Sequence[str],
) -> tuple[ActionGraphNode, ...]:
    if not phases or len(phases) != len(durations_s) or len(phases) != len(
        contact_expectations
    ):
        raise ValueError("motion graph phases, durations, and contact expectations must align")
    return tuple(
        ActionGraphNode(
            node_id=f"motion_{index:02d}_{phase}",
            kind=ActionNodeKind.MOTION,
            authority=NodeAuthority.HARDWARE,
            executor=NodeExecutor.DETERMINISTIC_TRANSPORT,
            control_mode=MotionControlMode.CARTESIAN_POSE,
            required_capabilities=("cartesian_pose", "force_guard", "contact_monitor"),
            timeout_s=duration_s,
            parameters={
                "segment_index": index,
                "phase": phase,
                "contact_expectation": contact_expectation,
            },
        )
        for index, (phase, duration_s, contact_expectation) in enumerate(
            zip(phases, durations_s, contact_expectations, strict=True)
        )
    )


def _compile_supervisor_nodes(
    observation_timeout_s: float,
    observation_capabilities: tuple[str, ...],
) -> _SupervisorNodes:
    return _SupervisorNodes(
        observe_initial=_observation_node(
            "observe_initial_object_pose",
            "precommand",
            observation_timeout_s,
            observation_capabilities,
        ),
        validate_observation=ActionGraphNode(
            node_id="validate_initial_observation",
            kind=ActionNodeKind.GUARD,
            authority=NodeAuthority.SUPERVISOR,
            executor=NodeExecutor.SUPERVISOR,
            required_capabilities=("observation_quality_guard",),
            timeout_s=observation_timeout_s,
            checkpoint_safe=True,
            parameters={"checks": "age_frame_source_covariance"},
        ),
        observe_final=_observation_node(
            "observe_terminal_object_pose",
            "terminal",
            observation_timeout_s,
            observation_capabilities,
        ),
        verify_goal=ActionGraphNode(
            node_id="verify_goal",
            kind=ActionNodeKind.VERIFY,
            authority=NodeAuthority.SUPERVISOR,
            executor=NodeExecutor.SUPERVISOR,
            required_capabilities=("task_goal_evaluator",),
            timeout_s=observation_timeout_s,
            checkpoint_safe=True,
            parameters={"on_miss": "return_to_agent"},
        ),
        complete=_terminal_node("terminal_completed", "completed"),
        return_to_agent=_terminal_node("terminal_return_to_agent", "return_to_agent"),
        safe_stop=_terminal_node("terminal_safe_stop", "safety_stopped", safe_stop=True),
    )


def _observation_node(
    node_id: str,
    phase: str,
    timeout_s: float,
    capabilities: tuple[str, ...],
) -> ActionGraphNode:
    return ActionGraphNode(
        node_id=node_id,
        kind=ActionNodeKind.OBSERVE,
        authority=NodeAuthority.REALITY,
        executor=NodeExecutor.PERCEPTION,
        required_capabilities=capabilities,
        timeout_s=timeout_s,
        checkpoint_safe=True,
        parameters={"observation": "object_pose", "phase": phase},
    )


def _terminal_node(node_id: str, outcome: str, *, safe_stop: bool = False) -> ActionGraphNode:
    return ActionGraphNode(
        node_id=node_id,
        kind=ActionNodeKind.SAFE_STOP if safe_stop else ActionNodeKind.TERMINAL,
        authority=NodeAuthority.HARDWARE if safe_stop else NodeAuthority.SUPERVISOR,
        executor=NodeExecutor.SUPERVISOR,
        timeout_s=TERMINAL_TRANSITION_TIMEOUT_S,
        checkpoint_safe=True,
        parameters={"outcome": outcome},
    )


def _entry_edges(
    nodes: _SupervisorNodes,
    first_motion: ActionGraphNode,
) -> tuple[ActionGraphEdge, ...]:
    return (
        _edge(nodes.observe_initial, nodes.validate_observation, EdgeGuard.ON_SUCCESS),
        _edge(nodes.observe_initial, nodes.safe_stop, EdgeGuard.ON_FAILURE),
        _edge(nodes.validate_observation, first_motion, EdgeGuard.ON_SUCCESS),
        _edge(nodes.validate_observation, nodes.safe_stop, EdgeGuard.ON_FAILURE),
    )


def _motion_edges(
    motion_nodes: tuple[ActionGraphNode, ...],
    nodes: _SupervisorNodes,
) -> tuple[ActionGraphEdge, ...]:
    edges: list[ActionGraphEdge] = []
    for index, node in enumerate(motion_nodes):
        success_target = (
            motion_nodes[index + 1]
            if index + 1 < len(motion_nodes)
            else nodes.observe_final
        )
        edges.extend(
            (
                _edge(node, success_target, EdgeGuard.ON_SUCCESS),
                _edge(node, nodes.safe_stop, EdgeGuard.ON_FAILURE),
                _edge(node, nodes.safe_stop, EdgeGuard.ON_SAFETY_TRIP),
            )
        )
    return tuple(edges)


def _exit_edges(nodes: _SupervisorNodes) -> tuple[ActionGraphEdge, ...]:
    return (
        _edge(nodes.observe_final, nodes.verify_goal, EdgeGuard.ON_SUCCESS),
        _edge(nodes.observe_final, nodes.safe_stop, EdgeGuard.ON_FAILURE),
        _edge(nodes.verify_goal, nodes.complete, EdgeGuard.ON_SUCCESS),
        _edge(nodes.verify_goal, nodes.return_to_agent, EdgeGuard.ON_GOAL_MISS),
        _edge(nodes.verify_goal, nodes.safe_stop, EdgeGuard.ON_FAILURE),
    )


def _edge(
    source: ActionGraphNode,
    target: ActionGraphNode,
    guard: EdgeGuard,
) -> ActionGraphEdge:
    return ActionGraphEdge(
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        guard=guard,
    )
