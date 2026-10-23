"""Versioned action-graph contracts below agent planning and above executors."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _GraphModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ActionNodeKind(StrEnum):
    OBSERVE = "observe"
    GUARD = "guard"
    MOTION = "motion"
    VERIFY = "verify"
    SAFE_STOP = "safe_stop"
    TERMINAL = "terminal"


class NodeAuthority(StrEnum):
    HARDWARE = "hardware"
    REALITY = "reality"
    SUPERVISOR = "supervisor"


class NodeExecutor(StrEnum):
    DETERMINISTIC_TRANSPORT = "deterministic_transport"
    PERCEPTION = "perception"
    SUPERVISOR = "supervisor"


class MotionControlMode(StrEnum):
    JOINT_POSITION = "joint_position"
    CARTESIAN_POSE = "cartesian_pose"
    CARTESIAN_VELOCITY = "cartesian_velocity"
    CARTESIAN_IMPEDANCE = "cartesian_impedance"
    JOINT_TORQUE = "joint_torque"


class NodeOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    SAFETY_TRIP = "safety_trip"
    GOAL_MISS = "goal_miss"


class EdgeGuard(StrEnum):
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"
    ON_SAFETY_TRIP = "on_safety_trip"
    ON_GOAL_MISS = "on_goal_miss"

    @classmethod
    def for_outcome(cls, outcome: NodeOutcome) -> EdgeGuard:
        return cls(f"on_{outcome.value}")


GraphParameter = str | int | float | bool


class ActionGraphNode(_GraphModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: ActionNodeKind
    authority: NodeAuthority
    executor: NodeExecutor
    control_mode: MotionControlMode | None = None
    required_capabilities: tuple[str, ...] = ()
    timeout_s: float = Field(gt=0.0)
    checkpoint_safe: bool = False
    parameters: dict[str, GraphParameter] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capabilities(self) -> ActionGraphNode:
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("action node capabilities must be unique")
        if self.kind is ActionNodeKind.MOTION and self.executor is not NodeExecutor.DETERMINISTIC_TRANSPORT:
            raise ValueError("physical motion nodes require the deterministic transport")
        if (self.kind is ActionNodeKind.MOTION) != (self.control_mode is not None):
            raise ValueError("exactly motion nodes must declare a control mode")
        if self.kind in {ActionNodeKind.TERMINAL, ActionNodeKind.SAFE_STOP} and (
            self.required_capabilities
        ):
            raise ValueError("terminal and safe-stop nodes cannot require capabilities")
        return self


class ActionGraphEdge(_GraphModel):
    source_node_id: str
    target_node_id: str
    guard: EdgeGuard


class ActionGraph(_GraphModel):
    graph_schema_version: Literal["action-graph-v1"] = "action-graph-v1"
    compiler_version: Literal["motion-plan-compiler-v1"] = "motion-plan-compiler-v1"
    graph_name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    task_id: str = Field(min_length=1)
    task_version: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    object_archetype_version: str = Field(min_length=1)
    geometry_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calibration_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protocol_binding_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entry_node_id: str
    terminal_node_ids: tuple[str, ...] = Field(min_length=1)
    wall_clock_deadline_s: float = Field(gt=0.0)
    nodes: tuple[ActionGraphNode, ...] = Field(min_length=1)
    edges: tuple[ActionGraphEdge, ...]

    @model_validator(mode="after")
    def validate_topology(self) -> ActionGraph:
        nodes_by_id = {node.node_id: node for node in self.nodes}
        if len(nodes_by_id) != len(self.nodes):
            raise ValueError("action graph node IDs must be unique")
        if self.entry_node_id not in nodes_by_id:
            raise ValueError("action graph entry node does not exist")
        if len(set(self.terminal_node_ids)) != len(self.terminal_node_ids):
            raise ValueError("action graph terminal IDs must be unique")
        if any(node_id not in nodes_by_id for node_id in self.terminal_node_ids):
            raise ValueError("action graph terminal node does not exist")
        if any(
            nodes_by_id[node_id].kind not in {ActionNodeKind.TERMINAL, ActionNodeKind.SAFE_STOP}
            for node_id in self.terminal_node_ids
        ):
            raise ValueError("terminal IDs must reference terminal or safe-stop nodes")
        if not any(
            nodes_by_id[node_id].kind is ActionNodeKind.SAFE_STOP
            for node_id in self.terminal_node_ids
        ):
            raise ValueError("action graph requires an absorbing safe-stop terminal")

        outgoing: dict[str, list[ActionGraphEdge]] = defaultdict(list)
        edge_keys: set[tuple[str, EdgeGuard]] = set()
        for edge in self.edges:
            if edge.source_node_id not in nodes_by_id or edge.target_node_id not in nodes_by_id:
                raise ValueError("action graph edge references an unknown node")
            key = (edge.source_node_id, edge.guard)
            if key in edge_keys:
                raise ValueError("action graph has ambiguous edges for one outcome")
            edge_keys.add(key)
            outgoing[edge.source_node_id].append(edge)

        for node in self.nodes:
            if node.node_id in self.terminal_node_ids:
                if outgoing[node.node_id]:
                    raise ValueError("terminal and safe-stop nodes must be absorbing")
                continue
            if not outgoing[node.node_id]:
                raise ValueError("every nonterminal node requires an outgoing edge")
            if node.kind is ActionNodeKind.MOTION:
                guards = {edge.guard for edge in outgoing[node.node_id]}
                if not {EdgeGuard.ON_FAILURE, EdgeGuard.ON_SAFETY_TRIP}.issubset(guards):
                    raise ValueError("every motion node requires failure and safety-stop edges")
                unsafe_targets = [
                    edge.target_node_id
                    for edge in outgoing[node.node_id]
                    if edge.guard in {EdgeGuard.ON_FAILURE, EdgeGuard.ON_SAFETY_TRIP}
                    and nodes_by_id[edge.target_node_id].kind is not ActionNodeKind.SAFE_STOP
                ]
                if unsafe_targets:
                    raise ValueError("motion failure and safety-trip edges must reach safe-stop")

        reachable = {self.entry_node_id}
        frontier = [self.entry_node_id]
        while frontier:
            current = frontier.pop()
            for edge in outgoing[current]:
                if edge.target_node_id not in reachable:
                    reachable.add(edge.target_node_id)
                    frontier.append(edge.target_node_id)
        if reachable != set(nodes_by_id):
            raise ValueError("action graph contains unreachable nodes")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("action graph v1 must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for edge in outgoing.get(node_id, ()):
                target = edge.target_node_id
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        visit(self.entry_node_id)
        return self

    @property
    def graph_id(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"

    def next_node(self, node_id: str, outcome: NodeOutcome) -> str | None:
        if node_id in self.terminal_node_ids:
            return None
        guard = EdgeGuard.for_outcome(outcome)
        matches = [
            edge
            for edge in self.edges
            if edge.source_node_id == node_id and edge.guard is guard
        ]
        if len(matches) != 1:
            raise ValueError(f"node {node_id} has no deterministic edge for {guard.value}")
        return matches[0].target_node_id
