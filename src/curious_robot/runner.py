"""Autonomous Observe→Experiment→Identify→Predict→Act→Verify episode runner."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import matplotlib
import numpy as np
import warp as wp
from matplotlib.animation import FuncAnimation, PillowWriter

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from .action_graph import MotionControlMode, NodeOutcome, compile_skill_action_graph
from .belief import initial_belief_for_object
from .environment import PhysicalParameters, StepControl, TruthEnvironment, WorldConfig
from .events import DiscoveryStage
from .objects import get_object_archetype, registered_planar_corner_error
from .planner_adapters import DeterministicPlannerAdapter, PlannerAdapter
from .planning import action_utility, candidate_actions
from .ports import RobotRealityAdapter
from .protocol import protocol_sha256
from .schemas import ActionResult, EpisodeResult, ParameterName, PhysicalBelief
from .surface import SurfaceFamily, SurfaceProfile, sample_surface_profile
from .sysid import (
    calibrate_coulomb_surrogate,
    estimate_dynamic_friction,
    estimate_mass,
    estimate_static_friction,
)
from .tasks import (
    PlanarPose,
    evaluate_planar_goal,
    get_task_definition,
    get_task_preset,
    wrapped_yaw_error,
    yaw_from_quaternion_xyzw,
)
from .tools import RobotTools
from .world_config import object_kind_from_archetype_id


class EventSink(Protocol):
    def emit(self, intent: dict[str, Any]) -> None: ...


class _EpisodeEvents:
    def __init__(self, sink: EventSink | None) -> None:
        self.sink = sink
        self.stage = DiscoveryStage.OBSERVE
        self.stage_instance_id = uuid4()
        self.iteration = 0
        self.started = time.monotonic()
        if sink is not None:
            self._emit_stage_entered()

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: UUID | None = None,
        sim_time_s: float | None = None,
    ) -> UUID:
        identifier = event_id or uuid4()
        if self.sink is not None:
            self.sink.emit(
                {
                    "event_id": str(identifier),
                    "type": event_type,
                    "stage": self.stage.value,
                    "iteration_id": self.iteration,
                    "stage_instance_id": str(self.stage_instance_id),
                    "sim_time_s": sim_time_s,
                    "payload": payload,
                }
            )
        return identifier

    def enter(self, stage: DiscoveryStage, *, iteration: int | None = None) -> None:
        if self.sink is not None:
            self.emit(
                "stage.completed",
                {
                    "stage_instance_id": str(self.stage_instance_id),
                    "iteration": self.iteration,
                    "next_stage": stage.value,
                    "outcome": "completed",
                    "evidence_ids": [],
                    "duration_s": max(0.0, time.monotonic() - self.started),
                },
            )
        self.stage = stage
        if iteration is not None:
            self.iteration = iteration
        self.stage_instance_id = uuid4()
        self.started = time.monotonic()
        self._emit_stage_entered()

    def action_node(
        self,
        graph_id: str,
        node_id: str,
        state: str,
        outcome: NodeOutcome | None = None,
    ) -> None:
        self.emit(
            "action.node_transitioned",
            {
                "graph_id": graph_id,
                "node_id": node_id,
                "state": state,
                "outcome": outcome.value if outcome is not None else None,
                "evidence_ids": [],
            },
        )

    def finish(self) -> None:
        if self.sink is not None:
            self.emit(
                "stage.completed",
                {
                    "stage_instance_id": str(self.stage_instance_id),
                    "iteration": self.iteration,
                    "next_stage": None,
                    "outcome": "completed",
                    "evidence_ids": [],
                    "duration_s": max(0.0, time.monotonic() - self.started),
                },
            )

    def _emit_stage_entered(self) -> None:
        self.emit(
            "stage.entered",
            {
                "stage_instance_id": str(self.stage_instance_id),
                "iteration": self.iteration,
                "prior_stage": None,
            },
        )


def _distribution(estimate) -> dict[str, Any]:
    return {
        "mean": estimate.mean,
        "uncertainty": {
            "standard_deviation": estimate.std,
            "interval95": estimate.interval95,
            "physical_bounds": estimate.physical_bounds,
        },
        "units": estimate.units,
    }


def _simulated_reality_calibration_id(task_id: str, geometry_hash: str) -> str:
    document = json.dumps(
        {
            "authority": "simulated-reality",
            "controller": "newton-franka-skill-v1",
            "geometry_hash": geometry_hash,
            "task_id": task_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(document.encode()).hexdigest()}"


def run_episode(
    *,
    task_id: str = "push-cube-v1",
    seed: int,
    output_directory: str | Path,
    device: str = "cuda:0",
    include_robot: bool = True,
    planner: PlannerAdapter | None = None,
    event_sink: EventSink | None = None,
    control_gate: StepControl | None = None,
    material_bounds: dict[str, object] | None = None,
) -> EpisodeResult:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    task = get_task_preset(task_id)
    task_definition = get_task_definition(task.task_definition_id, task.task_definition_version)
    archetype = get_object_archetype(task.object_archetype_id, task.object_archetype_version)
    object_kind = object_kind_from_archetype_id(archetype.archetype_id)
    support_height_m = -min(
        part.center_object_m[2] - part.half_extents_m[2] for part in archetype.parts
    )
    # Public seeds control controllers and observation noise. Hidden scenario
    # physics uses independent entropy so an open-source agent cannot derive
    # truth values from the public run configuration.
    private_seed_override = os.environ.get("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED")
    scenario_seed = (
        int(private_seed_override, 10) if private_seed_override is not None else secrets.randbits(63)
    )
    if not 0 <= scenario_seed < 2**63:
        raise ValueError("CURIOUS_ROBOT_EVALUATOR_SCENARIO_SEED must be in [0, 2**63)")
    scenario_rng = np.random.default_rng(scenario_seed)
    bounds = material_bounds or {}
    mass_range = bounds.get("mass_kg", task.default_mass_range_kg)
    if not isinstance(mass_range, (list, tuple)) or len(mass_range) != 2:
        raise ValueError("material_bounds.mass_kg must be [minimum, maximum]")
    mass_low, mass_high = (float(value) for value in mass_range)
    if not 0.02 <= mass_low <= mass_high <= 2.0:
        raise ValueError("material_bounds.mass_kg must satisfy 0.02 <= min <= max <= 2.0")
    family_value = bounds.get("surface_family")
    surface_family = SurfaceFamily(str(family_value)) if family_value else None
    mass = float(scenario_rng.uniform(mass_low, mass_high))
    surface = sample_surface_profile(scenario_seed + 1_000, surface_family)
    x_low, x_high, y_low, y_high = task.initial_position_jitter_m
    initial = (
        float(task.initial_pose.x_m + scenario_rng.uniform(x_low, x_high)),
        float(task.initial_pose.y_m + scenario_rng.uniform(y_low, y_high)),
        support_height_m,
    )
    yaw_low, yaw_high = task.initial_yaw_jitter_rad
    initial_yaw = float(task.initial_pose.yaw_rad + scenario_rng.uniform(yaw_low, yaw_high))
    truth = PhysicalParameters(
        mass,
        surface.effective_static_friction,
        surface.effective_dynamic_friction,
        com_m=archetype.uniform_density_centroid_m,
        restitution=surface.restitution,
    )
    scenario_commitment = hashlib.sha256(
        json.dumps(
            {
                "scenario_seed": scenario_seed,
                "truth": asdict(truth),
                "surface": asdict(surface),
                "initial_position_m": initial,
                "initial_yaw_rad": initial_yaw,
                "task_preset_id": task.preset_id,
                "task_preset_hash": task.preset_hash,
                "object_geometry_hash": archetype.geometry_hash,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    truth_config = WorldConfig(
        task_id=task.preset_id,
        device=device,
        initial_position_m=initial,
        initial_yaw_rad=initial_yaw,
        target_position_m=(task.goal.target.x_m, task.goal.target.y_m, support_height_m),
        target_radius_m=task.goal.position_tolerance_m,
        target_yaw_rad=task.goal.target.yaw_rad,
        target_yaw_tolerance_rad=task.goal.yaw_tolerance_rad,
        include_robot=include_robot,
        object_kind=object_kind,
        seed=seed,
    )
    episode_events = _EpisodeEvents(event_sink)
    reality = TruthEnvironment(
        truth,
        truth_config,
        control_gate=control_gate,
        event_sink=event_sink,
        scene="truth",
        scenario_id="simulated-reality",
    )
    initial_observation = reality.observe_object_pose()
    config = replace(
        truth_config,
        initial_position_m=initial_observation.pose.position_m,
        initial_yaw_rad=yaw_from_quaternion_xyzw(initial_observation.pose.orientation_xyzw),
    )
    task_belief = initial_belief_for_object(
        archetype.archetype_id,
        archetype.version,
    )
    tools = RobotTools(
        RobotRealityAdapter(reality),
        task_belief,
        config,
        control_gate=control_gate,
        event_sink=event_sink,
    )
    planner = planner or DeterministicPlannerAdapter()
    belief_history: list[PhysicalBelief] = [tools.belief]
    completed: set[str] = set()
    deployment_reality: TruthEnvironment | None = None
    try:
        _log("OBSERVE", "capturing robot, object, and target state")
        tools.observe_scene()
        experiment_iteration = 0
        for _ in range(task_definition.maximum_experiments):
            episode_events.enter(DiscoveryStage.REASON, iteration=experiment_iteration)
            baseline = tools.propose_experiment(completed)
            if baseline.selected:
                score = next(score for score in baseline.scores if score.candidate_id == baseline.selected)
                _log(
                    "REASON",
                    f"candidate={baseline.selected} utility={score.utility:.3f} "
                    f"information_gain={score.expected_information_gain:.3f}",
                )
            selected = planner.choose_experiment(
                {
                    "belief": tools.belief.model_dump(mode="json"),
                    "uncertainty": tools.estimate_parameter_uncertainty(),
                },
                baseline,
            )
            if selected is None:
                _log("STOP EXPERIMENTING", baseline.rationale)
                break
            episode_events.emit(
                "rationale.published",
                {
                    "summary": baseline.rationale
                    + "; scores="
                    + ", ".join(
                        f"{candidate.candidate_id}: EIG {candidate.expected_information_gain:.3f}, "
                        f"cost {candidate.cost_penalty:.3f}, risk {candidate.risk_penalty:.3f}, "
                        f"utility {candidate.utility:.3f}"
                        for candidate in baseline.scores
                    ),
                    "evidence_ids": list(tools.belief.supporting_evidence_ids),
                    "considered_option_ids": [score.candidate_id for score in baseline.scores],
                    "selected_option_id": selected,
                },
            )
            episode_events.enter(DiscoveryStage.IDENTIFY_UNKNOWNS)
            episode_events.enter(DiscoveryStage.DESIGN_EXPERIMENT)
            _log("ACT / MEASURE", f"running bounded experiment {selected}")
            completed.add(selected)
            episode_events.enter(DiscoveryStage.ACT)
            tool_call_id = uuid4()
            tool_started = time.monotonic()
            episode_events.emit(
                "tool.started",
                {"tool_call_id": str(tool_call_id), "tool_name": selected, "arguments": {}},
            )
            episode_events.enter(DiscoveryStage.MEASURE)
            if selected == "weigh_quasistatic":
                estimates = [estimate_mass(tools.weigh_object())]
            elif selected == "static_breakaway_ramp":
                estimates = [
                    estimate_static_friction(
                        tools.probe_static_friction(), tools.belief.parameters[ParameterName.MASS]
                    )
                ]
            elif selected == "dynamic_controlled_slide":
                estimates = [
                    estimate_dynamic_friction(
                        tools.probe_dynamic_friction(),
                        tools.belief.parameters[ParameterName.MASS],
                        tools.belief.parameters[ParameterName.STATIC_FRICTION],
                    )
                ]
            elif selected == "multi_orientation_com":
                estimates = [tools.estimate_center_of_mass()]
            else:
                raise RuntimeError(f"no validated experiment dispatcher for {selected}")
            evidence = tools.evidence[-1] if tools.evidence else None
            episode_events.emit(
                "tool.completed",
                {
                    "tool_call_id": str(tool_call_id),
                    "tool_name": selected,
                    "duration_s": max(0.0, time.monotonic() - tool_started),
                    "result": {
                        "evidence_id": evidence.evidence_id if evidence else None,
                        "quality_flags": list(evidence.quality_flags) if evidence else [],
                    },
                },
            )
            if evidence is not None:
                accepted_samples = [sample for sample in evidence.samples if sample.contact_valid]
                sample_count = min(40, len(evidence.samples))
                sample_indices = np.linspace(0, len(evidence.samples) - 1, sample_count, dtype=int)
                for sample_index in dict.fromkeys(int(index) for index in sample_indices):
                    sample = evidence.samples[sample_index]
                    measured_force = float(np.linalg.norm(sample.measured_force_n))
                    episode_events.emit(
                        "measurement.sampled",
                        {
                            "evidence_id": evidence.evidence_id,
                            "sensor_kind": "native_contact_force_and_camera_pose",
                            "producer": evidence.metadata.get(
                                "producer",
                                f"Newton SensorContact robot-tool ↔ {archetype.archetype_id}",
                            ),
                            "frame": "world",
                            "units": "SI",
                            "values": {
                                "measured_force_n": measured_force,
                                "object_position_m": float(sample.position_m[0]),
                                "object_velocity_mps": float(sample.velocity_m_s[0]),
                                "object_yaw_rad": yaw_from_quaternion_xyzw(
                                    sample.orientation_xyzw
                                ),
                                "object_yaw_rate_rad_s": float(
                                    sample.angular_velocity_rad_s[2]
                                ),
                            },
                            "valid": sample.contact_valid,
                        },
                        sim_time_s=sample.timestamp_s,
                    )
                episode_events.emit(
                    "measurement.summary",
                    {
                        "evidence_id": evidence.evidence_id,
                        "accepted_count": sum(sample.contact_valid for sample in evidence.samples),
                        "rejected_count": sum(not sample.contact_valid for sample in evidence.samples),
                        "aggregate_values": {
                            "median_force_n": float(
                                np.median(
                                    [np.linalg.norm(sample.measured_force_n) for sample in accepted_samples]
                                )
                            )
                            if accepted_samples
                            else 0.0
                        },
                        "quality_flags": list(evidence.quality_flags),
                    },
                )
            episode_events.enter(DiscoveryStage.SYSTEM_IDENTIFICATION)
            prior_estimates = {
                estimate.name: tools.belief.parameters[estimate.name] for estimate in estimates
            }
            tools.update_physical_parameters(estimates)
            belief_history.append(tools.belief)
            for estimate in estimates:
                episode_events.emit(
                    "belief.updated",
                    {
                        "revision": tools.belief.revision,
                        "parameter_name": estimate.name.value,
                        "prior": _distribution(prior_estimates[estimate.name]),
                        "posterior": _distribution(estimate),
                        "method": estimate.method,
                        "evidence_ids": list(estimate.evidence_ids),
                        "assumptions": list(estimate.assumptions),
                        "mismatch_notes": list(estimate.mismatch_notes),
                    },
                )
            episode_events.enter(DiscoveryStage.UPDATE_SIMULATION)
            if tools.belief_world is not None:
                episode_events.emit(
                    "simulation.belief_rebuilt",
                    {
                        "belief_revision": tools.belief.revision,
                        "world_instance_id": tools.belief_world.world_instance_id,
                        "body_manifest_id": f"franka-{archetype.archetype_id}-v1",
                        "task_id": task.preset_id,
                        "object_geometry_hash": archetype.geometry_hash,
                        "parameter_names": [parameter.value for parameter in ParameterName],
                    },
                )
            for estimate in estimates:
                _log(
                    "LEARN",
                    f"{estimate.name.value}={estimate.mean:.4f} ± {1.96 * estimate.std:.4f}; "
                    f"belief revision={tools.belief.revision}",
                )
            experiment_iteration += 1

        episode_events.enter(DiscoveryStage.PREDICT, iteration=experiment_iteration)
        _log("PREDICT", "rolling out independent task candidates in learned Newton worlds")
        actions = candidate_actions(config.target_position_m, task_id=task.preset_id)
        outcomes = [tools.simulate_candidate_action(action) for action in actions]
        for outcome in outcomes:
            episode_events.emit(
                "prediction.completed",
                {
                    "action_id": outcome.action_id,
                    "scenario_id": outcome.action_id,
                    "final_position_m": outcome.final_position_m,
                    "final_orientation_xyzw": outcome.final_orientation_xyzw,
                    "target_error_m": outcome.target_error_m,
                    "target_yaw_error_rad": outcome.target_yaw_error_rad,
                    "success_probability": outcome.success_probability,
                    "peak_force_n": outcome.peak_force_n,
                    "tipping_margin": outcome.tipping_margin,
                    "feasible": outcome.feasible,
                    "reasons": [outcome.rationale],
                    "world_instance_id": (
                        str(outcome.world_instance_id) if outcome.world_instance_id is not None else None
                    ),
                    "world_config_id": outcome.world_config_id,
                    "experiment_protocol_id": outcome.experiment_protocol_id,
                    "belief_revision": outcome.belief_revision,
                    "belief_snapshot_id": outcome.belief_snapshot_id,
                },
            )
            _log(
                "PREDICT",
                f"{outcome.action_id}: success={outcome.success_probability:.2f}, "
                f"target_error={outcome.target_error_m:.3f} m, peak_force={outcome.peak_force_n:.2f} N",
            )
        decision = tools.select_next_action(outcomes)
        episode_events.enter(DiscoveryStage.SELECT_ACTION)
        selection_rationale_id = episode_events.emit(
            "rationale.published",
            {
                "summary": decision.rationale,
                "evidence_ids": list(tools.belief.supporting_evidence_ids),
                "considered_option_ids": [outcome.action_id for outcome in outcomes],
                "selected_option_id": decision.selected_action_id,
            },
        )
        episode_events.emit(
            "action.selected",
            {
                "selected_action_id": decision.selected_action_id,
                "candidate_scores": {outcome.action_id: action_utility(outcome) for outcome in outcomes},
                "rationale_event_id": str(selection_rationale_id),
            },
        )
        selected_action = next(
            action for action in actions if action.action_id == decision.selected_action_id
        )
        _log("SELECT", f"{selected_action.action_id}: {decision.rationale}")

        if tools.belief_world is not None:
            tools.belief_world.close()
            tools.belief_world = None
        reality.close()
        deployment_reality = TruthEnvironment(
            truth,
            config,
            control_gate=control_gate,
            event_sink=event_sink,
            scene="truth",
            scenario_id="fresh-policy-deployment",
        )
        episode_events.enter(DiscoveryStage.EXECUTE)
        skill_capabilities = (
            ("cartesian_pose", "force_guard", "contact_monitor")
            if selected_action.kind == "push"
            else ("cartesian_pose", "gripper_position", "force_guard")
        )
        skill_timeout_s = max(
            1.0,
            tools.protocol.final_push.maximum_travel_m / selected_action.speed_m_s
            + 6.0 * config.maximum_cartesian_settle_s,
        )
        action_graph = compile_skill_action_graph(
            task_id=task.preset_id,
            task_version=task.version,
            object_id=f"unknown-{archetype.archetype_id}",
            object_archetype_version=archetype.version,
            geometry_hash=archetype.geometry_hash,
            calibration_id=_simulated_reality_calibration_id(
                task.preset_id,
                archetype.geometry_hash,
            ),
            protocol_binding_id=f"sha256:{protocol_sha256(tools.protocol)}",
            skill_id=selected_action.action_id,
            control_mode=MotionControlMode.CARTESIAN_POSE,
            required_capabilities=skill_capabilities,
            observation_timeout_s=config.maximum_cartesian_settle_s,
            skill_timeout_s=skill_timeout_s,
        )
        episode_events.emit(
            "action.graph_compiled",
            {
                "graph_id": action_graph.graph_id,
                "graph": action_graph.model_dump(mode="json", exclude_none=False),
            },
        )
        episode_events.action_node(
            action_graph.graph_id,
            "observe_initial_object_pose",
            "entered",
        )
        deployment_reality.settle(0.25)
        deployment_observation = deployment_reality.observe_object_pose()
        episode_events.action_node(
            action_graph.graph_id,
            "observe_initial_object_pose",
            "completed",
            NodeOutcome.SUCCESS,
        )
        deployment_config = replace(
            config,
            initial_position_m=deployment_observation.pose.position_m,
            initial_yaw_rad=yaw_from_quaternion_xyzw(
                deployment_observation.pose.orientation_xyzw
            ),
        )
        deployment_tools = RobotTools(
            RobotRealityAdapter(deployment_reality),
            tools.belief,
            deployment_config,
            protocol=tools.protocol,
            control_gate=control_gate,
            event_sink=event_sink,
        )
        episode_events.action_node(
            action_graph.graph_id,
            "execute_selected_skill",
            "entered",
        )
        if selected_action.kind == "pick_place":
            deployment_tools.grasp_object()
        initial_position, initial_orientation, initial_velocity = (
            deployment_reality.cube_controller_state()
        )
        initial_final_pose = tuple(float(v) for v in initial_position)
        initial_body_q = (*initial_final_pose, *(float(v) for v in initial_orientation))
        initial_body_qd = tuple(float(v) for v in initial_velocity)
        initial_joint_q = tuple(float(v) for v in deployment_reality.state_0.joint_q.numpy())
        initial_joint_qd = tuple(float(v) for v in deployment_reality.state_0.joint_qd.numpy())
        initial_joint_target_q = tuple(
            float(v) for v in deployment_reality.control.joint_target_q.numpy()
        )
        deployment_reality.clear_trace()
        replay_path = output / "scene_replay.bin"
        deployment_reality.start_recording(replay_path, stride=4)
        controller_result = "completed"
        safety_event_ids: list[str] = []
        if selected_action.kind == "push":
            execution = deployment_tools.execute_safe_push(
                selected_action.target_m,
                selected_action.max_force_n,
                speed_m_s=selected_action.speed_m_s,
                push_height_m=selected_action.push_height_m,
                lateral_contact_offset_m=selected_action.lateral_contact_offset_m,
                contact_surface_id=selected_action.contact_surface_id,
            )
            final_samples = list(execution.samples)
            controller_result = execution.outcome.value
            if execution.safety_tripped:
                safety_event_ids.append(
                    str(
                        episode_events.emit(
                            "safety.triggered",
                            {
                                "condition": "tool contact force norm exceeded action limit",
                                "measured_value": execution.peak_contact_force_n,
                                "limit": selected_action.max_force_n,
                                "units": "N",
                                "controller_response": "latched measured joints and stopped push",
                                "can_continue": False,
                            },
                        )
                    )
                )
        else:
            final_samples = deployment_reality.execute_pick_place(
                selected_action.target_m,
                selected_action.max_force_n,
                assumed_mass_kg=tools.belief.parameters[ParameterName.MASS].mean,
            )
        deployment_reality.stop_recording()
        final_control_trace = list(deployment_reality.control_trace)
        final_state_trace = list(deployment_reality.state_trace)
        final_orientation_trace = list(deployment_reality.orientation_trace)
        if selected_action.kind == "pick_place":
            deployment_tools.release_object()
        if controller_result == "completed":
            episode_events.action_node(
                action_graph.graph_id,
                "execute_selected_skill",
                "completed",
                NodeOutcome.SUCCESS,
            )
        elif selected_action.kind == "push" and execution.safety_tripped:
            episode_events.action_node(
                action_graph.graph_id,
                "execute_selected_skill",
                "safety_trip",
                NodeOutcome.SAFETY_TRIP,
            )
        else:
            episode_events.action_node(
                action_graph.graph_id,
                "execute_selected_skill",
                "failed",
                NodeOutcome.FAILURE,
            )
        episode_events.action_node(
            action_graph.graph_id,
            "observe_terminal_object_pose",
            "entered",
        )
        observed_pose = deployment_reality.observe_object_pose().pose
        episode_events.action_node(
            action_graph.graph_id,
            "observe_terminal_object_pose",
            "completed",
            NodeOutcome.SUCCESS,
        )
        observed = observed_pose.position_m
        episode_events.emit(
            "action.executed",
            {
                "action_id": selected_action.action_id,
                "controller_result": controller_result,
                "observed_terminal_position_m": observed,
                "observed_terminal_orientation_xyzw": observed_pose.orientation_xyzw,
                "safety_event_ids": safety_event_ids,
            },
        )
        selected_outcome = next(o for o in outcomes if o.action_id == selected_action.action_id)
        predicted = selected_outcome.final_position_m
        prediction_error = tools.evaluate_prediction_error(predicted, observed)
        observed_goal = evaluate_planar_goal(
            task.goal,
            PlanarPose(
                x_m=observed[0],
                y_m=observed[1],
                yaw_rad=yaw_from_quaternion_xyzw(observed_pose.orientation_xyzw),
            ),
        )
        predicted_yaw = yaw_from_quaternion_xyzw(selected_outcome.final_orientation_xyzw)
        observed_yaw = yaw_from_quaternion_xyzw(observed_pose.orientation_xyzw)
        prediction_yaw_error = wrapped_yaw_error(observed_yaw, predicted_yaw)
        shape_error = registered_planar_corner_error(
            archetype,
            observed_xy_yaw=(observed[0], observed[1], observed_yaw),
            target_xy_yaw=(
                task.goal.target.x_m,
                task.goal.target.y_m,
                task.goal.target.yaw_rad,
            ),
        )
        episode_events.enter(DiscoveryStage.VERIFY)
        episode_events.action_node(action_graph.graph_id, "verify_goal", "entered")
        success = deployment_tools.verify_task_success() and controller_result == "completed"
        verification = deployment_tools.records[-1].result_summary
        tools.records.extend(deployment_tools.records)
        episode_events.emit(
            "verification.completed",
            {
                "success": success,
                "goal_predicates": {
                    **observed_goal.predicates,
                    "stable": bool(verification["stable"]),
                },
                "prediction_residual_m": prediction_error,
                "observed_position_error_m": observed_goal.position_error_m,
                "observed_yaw_error_rad": observed_goal.yaw_error_rad,
                "maximum_registered_corner_error_m": shape_error.maximum_corner_error_m,
                "rms_registered_corner_error_m": shape_error.rms_corner_error_m,
                "prediction_yaw_residual_rad": prediction_yaw_error,
                "tolerance_m": config.target_radius_m,
                "yaw_tolerance_rad": task.goal.yaw_tolerance_rad,
            },
        )
        verification_outcome = NodeOutcome.SUCCESS if success else NodeOutcome.GOAL_MISS
        episode_events.action_node(
            action_graph.graph_id,
            "verify_goal",
            "completed" if success else "goal_miss",
            verification_outcome,
        )
        terminal_node_id = (
            "terminal_completed"
            if success
            else "terminal_safe_stop"
            if controller_result != "completed"
            else "terminal_return_to_agent"
        )
        episode_events.action_node(action_graph.graph_id, terminal_node_id, "entered")
        episode_events.action_node(
            action_graph.graph_id,
            terminal_node_id,
            "completed",
            NodeOutcome.SUCCESS,
        )
        _log(
            "VERIFY",
            f"success={success}, prediction_error={prediction_error:.4f} m, observed_position={observed}",
        )
        episode_events.enter(DiscoveryStage.LEARN)
        if prediction_error > config.prediction_residual_tolerance_m:
            affected = (
                ParameterName.DYNAMIC_FRICTION if selected_action.kind == "push" else ParameterName.MASS
            )
            old = tools.belief.parameters[affected]
            widened_std = min(old.std * 1.5, (old.physical_bounds[1] - old.physical_bounds[0]) / 2)
            revised = old.model_copy(
                update={
                    "std": widened_std,
                    "interval95": (
                        max(old.physical_bounds[0], old.mean - 1.96 * widened_std),
                        min(old.physical_bounds[1], old.mean + 1.96 * widened_std),
                    ),
                    "mismatch_notes": (
                        *old.mismatch_notes,
                        "final action prediction residual "
                        f"{prediction_error:.4f} m exceeded configured "
                        f"{config.prediction_residual_tolerance_m:.4f} m",
                    ),
                }
            )
            tools.update_physical_parameters([revised])
            belief_history.append(tools.belief)
            _log("LEARN", f"prediction mismatch widened {affected.value} uncertainty")
            episode_events.emit(
                "learning.completed",
                {
                    "affected_parameters": [affected.value],
                    "old_uncertainty": {affected.value: old.std},
                    "new_uncertainty": {affected.value: revised.std},
                    "residual_evidence_ids": [],
                    "follow_up_decision": "widened uncertainty after prediction mismatch",
                },
            )
        else:
            episode_events.emit(
                "learning.completed",
                {
                    "affected_parameters": [],
                    "old_uncertainty": {},
                    "new_uncertainty": {},
                    "residual_evidence_ids": [],
                    "follow_up_decision": "belief retained; prediction residual within tolerance",
                },
            )
        action_result = ActionResult(
            action_id=selected_action.action_id,
            observed_final_position_m=observed,
            predicted_final_position_m=predicted,
            observed_final_orientation_xyzw=observed_pose.orientation_xyzw,
            predicted_final_orientation_xyzw=selected_outcome.final_orientation_xyzw,
            prediction_error_m=prediction_error,
            prediction_yaw_error_rad=prediction_yaw_error,
            success=success,
        )
        result = EpisodeResult(
            seed=seed,
            success=success,
            selected_action=selected_action.action_id,
            experiment_count=len(completed),
            final_belief=tools.belief,
            action_result=action_result,
            output_directory=str(output.resolve()),
        )
        _write_artifacts(
            output,
            result,
            truth,
            surface,
            config,
            tools,
            belief_history,
            outcomes,
            decision.model_dump(mode="json"),
            selected_action.model_dump(mode="json"),
            initial_final_pose,
            initial_body_q,
            initial_body_qd,
            initial_joint_q,
            initial_joint_qd,
            initial_joint_target_q,
            final_control_trace,
            final_state_trace,
            final_orientation_trace,
            final_samples,
            scenario_seed,
            scenario_commitment,
        )
        episode_events.finish()
        return result
    finally:
        if tools.belief_world is not None:
            tools.belief_world.close()
        if deployment_reality is not None:
            deployment_reality.close()
        reality.close()


def _write_artifacts(
    output: Path,
    result: EpisodeResult,
    truth: PhysicalParameters,
    surface: SurfaceProfile,
    config: WorldConfig,
    tools: RobotTools,
    belief_history: list[PhysicalBelief],
    outcomes,
    decision: dict,
    selected_action: dict,
    initial_final_pose: tuple[float, float, float],
    initial_body_q: tuple[float, ...],
    initial_body_qd: tuple[float, ...],
    initial_joint_q: tuple[float, ...],
    initial_joint_qd: tuple[float, ...],
    initial_joint_target_q: tuple[float, ...],
    final_control_trace: list[tuple[float, ...]],
    final_state_trace: list[tuple[float, float, float]],
    final_orientation_trace: list[tuple[float, float, float, float]],
    final_samples,
    scenario_seed: int,
    scenario_commitment: str,
) -> None:
    task = get_task_preset(config.task_id)
    archetype = get_object_archetype(task.object_archetype_id, task.object_archetype_version)
    versions = {
        name: importlib.metadata.version(name)
        for name in ("newton", "warp-lang", "mujoco", "mujoco-warp", "numpy", "scipy", "pydantic")
    }
    metadata = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "device": str(wp.get_device(config.device)),
        "packages": versions,
        "solver": "newton.solvers.SolverMuJoCo / MuJoCo Warp",
        "dt_s": config.dt_s,
        "deterministic_mode": "RUN_TO_RUN",
        "scenario_commitment": scenario_commitment,
        "task": task.model_dump(mode="json"),
        "task_preset_hash": task.preset_hash,
        "object_archetype": archetype.model_dump(mode="json"),
        "object_geometry_hash": archetype.geometry_hash,
        "world_config": asdict(config),
        "experiment_protocol": tools.protocol.model_dump(mode="json"),
        "limitations": [
            "GPU reductions may not be bitwise deterministic across hardware or driver versions",
            "MuJoCo contact dynamics are calibrated by black-box optimization, not autodiff",
        ],
    }
    (output / "episode_result.json").write_text(result.model_dump_json(indent=2) + "\n")
    (output / "decision_timeline.json").write_text(
        json.dumps(
            {
                "tool_calls": [record.model_dump(mode="json") for record in tools.records],
                "belief_history": [belief.model_dump(mode="json") for belief in belief_history],
                "candidate_outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
                "final_decision": decision,
            },
            indent=2,
        )
        + "\n"
    )
    with (output / "measurements.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "evidence_id",
                "experiment",
                "timestamp_s",
                "x_m",
                "y_m",
                "z_m",
                "vx_m_s",
                "yaw_rad",
                "yaw_rate_rad_s",
                "force_x_n",
                "measured_normal_n",
                "contact_valid",
                "contact_pair",
                "contact_x_m",
                "contact_y_m",
                "contact_z_m",
                "contact_force_producer",
                "experiment_correlation_id",
                "command_id",
                "experiment_phase",
                "protocol_schema_version",
                "protocol_sha256",
            ]
        )
        for evidence in tools.evidence:
            for sample in evidence.samples:
                writer.writerow(
                    [
                        evidence.evidence_id,
                        evidence.experiment,
                        sample.timestamp_s,
                        *sample.position_m,
                        sample.velocity_m_s[0],
                        yaw_from_quaternion_xyzw(sample.orientation_xyzw),
                        sample.angular_velocity_rad_s[2],
                        sample.measured_force_n[0],
                        sample.measured_normal_load_n,
                        sample.contact_valid,
                        "|".join(sample.contact_pair) if sample.contact_pair else None,
                        *(sample.contact_position_m or (None, None, None)),
                        sample.contact_force_producer,
                        sample.experiment_id,
                        sample.command_id,
                        sample.experiment_phase,
                        evidence.metadata.get("protocol_schema_version"),
                        evidence.metadata.get("protocol_sha256"),
                    ]
                )
    evaluator_truth = asdict(truth)
    evaluator_truth["surface_profile"] = asdict(surface)
    manifest = {
        "schema_version": 1,
        "seed": config.seed,
        "metadata": metadata,
        "world_config": asdict(config),
        "solver_config": {"iterations": 20, "nconmax": 256, "njmax": 512},
        "evaluation_truth": evaluator_truth,
        "private_scenario_seed": scenario_seed,
        "task_id": task.preset_id,
        "object_archetype_id": archetype.archetype_id,
        "object_geometry_hash": archetype.geometry_hash,
        "initial_object_position_m": initial_final_pose,
        "initial_object_body_q": initial_body_q,
        "initial_object_body_qd": initial_body_qd,
        # Legacy cube keys keep v1 replay readers compatible. New readers use
        # the object-neutral fields above.
        "initial_cube_position_m": initial_final_pose,
        "initial_cube_body_q": initial_body_q,
        "initial_cube_body_qd": initial_body_qd,
        "initial_joint_q": initial_joint_q,
        "initial_joint_qd": initial_joint_qd,
        "initial_joint_target_q": initial_joint_target_q,
        "selected_action": selected_action,
        "action_controller": {
            "assumed_mass_kg": result.final_belief.parameters[ParameterName.MASS].mean,
        },
        "control_sequence_joint_target_q": final_control_trace,
        "reference_trajectory_position_m": final_state_trace,
        "reference_trajectory_orientation_xyzw": final_orientation_trace,
        "position_tolerance_m": 0.015 if config.include_robot else 0.005,
        "reexecution_yaw_tolerance_rad": config.reexecution_yaw_tolerance_rad,
        "agent_tool_calls": [record.model_dump(mode="json") for record in tools.records],
        "belief_history": [belief.model_dump(mode="json") for belief in belief_history],
        "sensor_configuration": {
            "pose_noise_std_m": config.observation_noise_std,
            "force_noise_std_n": config.force_noise_std,
            "force_producer": (
                f"Newton SensorContact CONTACT_F robot-tool ↔ {archetype.archetype_id}"
            ),
        },
    }
    (output / "reexecution.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (output / "experiment_protocol.json").write_text(
        json.dumps(tools.protocol.model_dump(mode="json"), indent=2) + "\n"
    )
    rollout_revision = next(
        (outcome.belief_revision for outcome in outcomes if outcome.belief_revision is not None),
        result.final_belief.revision,
    )
    rollout_belief = next(belief for belief in belief_history if belief.revision == rollout_revision)
    rollout_records = [
        {
            "arguments": record.arguments,
            "outcome": record.result_summary,
        }
        for record in tools.records
        if record.tool == "simulate_candidate_action"
    ]
    (output / "belief_simulation_config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "physical_parameters_source": "posterior",
                "known_scene_and_numerical_config_source": "versioned WorldConfig",
                "belief_revision": rollout_belief.revision,
                "physical_belief": rollout_belief.model_dump(mode="json"),
                "candidate_rollouts": rollout_records,
                "post_execution_belief": result.final_belief.model_dump(mode="json"),
                "world_config": asdict(config),
                "solver": {
                    "name": "SolverMuJoCo",
                    "iterations": 20,
                    "contact_capacity": 256,
                    "constraint_capacity": 512,
                },
            },
            indent=2,
        )
        + "\n"
    )
    (output / "value_provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "known_model_constants": {
                    "role": "versioned assumptions; never inferred from an episode",
                    "examples": [
                        "versioned object geometry",
                        "gravity convention",
                        "Franka kinematic description",
                    ],
                    "runtime_values_artifact": "metadata.json#/world_config",
                },
                "experiment_protocol": {
                    "role": "operator-selected, bounded robot procedure; not a learned physical property",
                    "source": "src/curious_robot/configs/physical_experiments.json",
                    "run_snapshot": "experiment_protocol.json",
                    "schema_version": tools.protocol.schema_version,
                    "sha256": protocol_sha256(tools.protocol),
                },
                "learned_physical_parameters": {
                    "role": "derived only from recorded robot observations by system identification",
                    "runtime_input": "in-memory typed ExperimentEvidence records",
                    "serialized_evidence": "measurements.csv",
                    "posterior": "belief_simulation_config.json#/physical_belief",
                    "belief_world_input": "belief_simulation_config.json",
                },
                "evaluator_only_hidden_parameters": {
                    "role": "sampled simulated-reality secret; forbidden from agent/controller/SysID inputs",
                    "location": "reexecution.json#/evaluation_truth",
                    "availability": "terminal evaluator artifact only",
                },
                "dataflow": [
                    "experiment_protocol.json -> robot joint targets",
                    "robot observations -> measurements.csv",
                    "typed ExperimentEvidence -> posterior belief",
                    "posterior belief -> belief_simulation_config.json -> independent Newton belief world",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    report_lines = [
        "# Robot physical experiment report",
        "",
        f"- Run seed: `{result.seed}`",
        "- Reality boundary: hidden-parameter Newton plant standing in for the future physical workspace",
        "- Agent boundary: robot commands, camera/object pose, joint/FK state, and native contact force only",
        "- Belief boundary: independently constructed Newton model from posterior estimates",
        "",
        "## Experiments",
        "",
    ]
    for evidence in tools.evidence:
        accepted_samples = [sample for sample in evidence.samples if sample.contact_valid]
        forces = [abs(sample.measured_force_n[0]) for sample in accepted_samples]
        displacement = (
            evidence.samples[-1].position_m[0] - evidence.samples[0].position_m[0]
            if evidence.samples
            else 0.0
        )
        report_lines.extend(
            [
                f"### {evidence.experiment} — `{evidence.evidence_id}`",
                "",
                f"- Producer: {evidence.metadata.get('producer', 'declared in sensor configuration')}",
                f"- Controller: {evidence.metadata.get('controller', 'n/a')}",
                f"- Raw samples: {len(evidence.samples)}",
                f"- Accepted native-contact samples: {len(accepted_samples)}",
                f"- Rejected non-contact samples: {len(evidence.samples) - len(accepted_samples)}",
                f"- Contact coverage: {len(accepted_samples) / max(len(evidence.samples), 1):.1%}",
                f"- Peak measured horizontal contact force: {max(forces, default=0.0):.3f} N",
                f"- Observed horizontal displacement: {displacement:.4f} m",
                f"- Quality flags: {', '.join(evidence.quality_flags) or 'none'}",
                "",
            ]
        )
    report_lines.extend(["## Posterior", ""])
    for name, estimate in result.final_belief.parameters.items():
        report_lines.append(
            f"- `{name.value}`: {estimate.mean:.4f} ± {1.96 * estimate.std:.4f} {estimate.units}; "
            f"status={estimate.status}; method={estimate.method}"
        )
        for mismatch in estimate.mismatch_notes:
            report_lines.append(f"  - Model mismatch: {mismatch}")
    report_lines.extend(
        [
            "",
            "## Decision and verification",
            "",
            f"- Selected action: `{result.selected_action}`",
            f"- Task success: `{result.success}`",
            f"- Prediction residual: {result.action_result.prediction_error_m:.4f} m",
            "",
            "> Ground-truth values are intentionally excluded from this agent-facing report. "
            "They exist only in terminal evaluator artifacts.",
        ]
    )
    (output / "experiment_report.md").write_text("\n".join(report_lines) + "\n")
    dynamic_evidence = next(
        (evidence for evidence in reversed(tools.evidence) if evidence.experiment == "dynamic_friction"),
        None,
    )
    if dynamic_evidence is not None:
        calibration = calibrate_coulomb_surrogate(
            dynamic_evidence,
            result.final_belief.parameters[ParameterName.MASS].mean,
            result.final_belief.parameters[ParameterName.DYNAMIC_FRICTION].mean,
            bounds=(0.1, min(0.9, result.final_belief.parameters[ParameterName.STATIC_FRICTION].mean)),
        )
        (output / "calibration.json").write_text(json.dumps(calibration, indent=2) + "\n")
    _plot_parameters(output, truth, result.final_belief)
    _plot_trajectory(output, outcomes, final_samples, config.target_position_m)
    _plot_final(
        output,
        initial_final_pose,
        result.action_result.observed_final_position_m,
        config.target_position_m,
    )
    _write_timeline_html(output, result, tools, belief_history)
    _write_discovery_gif(output, belief_history, truth)


def _log(stage: str, message: str) -> None:
    print(f"[Curious Robot · {stage}] {message}", flush=True)


def _plot_parameters(output: Path, truth: PhysicalParameters, belief: PhysicalBelief) -> None:
    names = [ParameterName.MASS, ParameterName.STATIC_FRICTION, ParameterName.DYNAMIC_FRICTION]
    true_values = [truth.mass_kg, truth.static_friction, truth.dynamic_friction]
    estimated = [belief.parameters[name].mean for name in names]
    errors = [1.96 * belief.parameters[name].std for name in names]
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(3)
    ax.bar(x - 0.18, true_values, 0.36, label="hidden truth (evaluator only)")
    ax.bar(x + 0.18, estimated, 0.36, yerr=errors, label="agent estimate")
    ax.set_xticks(x, ["mass [kg]", "static μ", "dynamic μ"])
    ax.legend()
    ax.set_title("Estimated versus hidden physical parameters")
    fig.tight_layout()
    fig.savefig(output / "estimated_vs_true.png", dpi=160)
    plt.close(fig)


def _plot_trajectory(output: Path, outcomes, samples, target) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    observed = np.asarray([sample.position_m for sample in samples])
    if len(observed):
        ax.plot(observed[:, 0], observed[:, 1], label="observed final action")
    for outcome in outcomes:
        ax.scatter(*outcome.final_position_m[:2], label=f"predicted {outcome.action_id}")
    ax.scatter(target[0], target[1], marker="*", s=160, label="target")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.axis("equal")
    ax.legend(fontsize=8)
    ax.set_title("Predicted versus observed trajectory")
    fig.tight_layout()
    fig.savefig(output / "predicted_vs_observed.png", dpi=160)
    plt.close(fig)


def _plot_final(output: Path, initial, final, target) -> None:
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.scatter(initial[0], initial[1], label="start")
    ax.scatter(final[0], final[1], label="final")
    circle = plt.Circle(target[:2], 0.035, fill=False, color="green", label="target region")
    ax.add_patch(circle)
    ax.set_xlim(-0.22, 0.22)
    ax.set_ylim(-0.12, 0.12)
    ax.axis("equal")
    ax.legend()
    ax.set_title("Final manipulation result")
    fig.tight_layout()
    fig.savefig(output / "final_result.png", dpi=160)
    plt.close(fig)


def _write_timeline_html(output: Path, result: EpisodeResult, tools: RobotTools, belief_history) -> None:
    rows = []
    for record in tools.records:
        summary = json.dumps(record.result_summary, indent=2)
        rows.append(
            f"<tr><td>{record.sequence}</td><td>{record.timestamp_s:.3f}</td>"
            f"<td><code>{record.tool}</code></td><td><pre>{summary}</pre></td></tr>"
        )
    belief_cards = []
    tracked = {ParameterName.MASS, ParameterName.STATIC_FRICTION, ParameterName.DYNAMIC_FRICTION}
    for belief in belief_history:
        values = " · ".join(
            f"{name.value}={estimate.mean:.3f}±{estimate.std:.3f}"
            for name, estimate in belief.parameters.items()
            if name in tracked
        )
        belief_cards.append(f"<li><strong>Revision {belief.revision}</strong> — {values}</li>")
    state_class = "ok" if result.success else ""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Curious Robot discovery timeline</title>
<style>body{{font:15px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;
background:#0c111b;color:#e8eef9}}
h1,h2{{color:#78dce8}} .hero{{font-size:1.3rem}} table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #28354a;padding:8px;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;margin:0}}
.ok{{color:#a9dc76}} img{{max-width:46%;background:white;border-radius:8px;margin:8px}}</style></head>
<body><h1>Curious Robot — Discovery Timeline</h1>
<p class="hero">Observe → Reason → Experiment → Identify → Simulate → Act → Verify → Learn</p>
<h2>Outcome</h2><p class="{state_class}">Success: {result.success} · action: {result.selected_action}</p>
<h2>Belief evolution</h2><ol>{"".join(belief_cards)}</ol>
<h2>Evidence</h2><img src="estimated_vs_true.png"><img src="predicted_vs_observed.png">
<img src="final_result.png">
<h2>Inspectable tool calls</h2><table><tr><th>#</th><th>sim time</th><th>tool</th>
<th>result</th></tr>{"".join(rows)}</table>
</body></html>"""
    (output / "discovery_timeline.html").write_text(html)


def _write_discovery_gif(
    output: Path, belief_history: list[PhysicalBelief], truth: PhysicalParameters
) -> None:
    names = [ParameterName.MASS, ParameterName.STATIC_FRICTION, ParameterName.DYNAMIC_FRICTION]
    truth_values = [truth.mass_kg, truth.static_friction, truth.dynamic_friction]
    fig, ax = plt.subplots(figsize=(7, 4))

    def draw(frame: int):
        ax.clear()
        belief = belief_history[frame]
        estimates = [belief.parameters[name].mean for name in names]
        errors = [1.96 * belief.parameters[name].std for name in names]
        x = np.arange(3)
        ax.bar(x - 0.18, truth_values, 0.36, label="truth (evaluator)")
        ax.bar(x + 0.18, estimates, 0.36, yerr=errors, label="agent belief")
        ax.set_xticks(x, ["mass", "static μ", "dynamic μ"])
        ax.set_ylim(0, max(1.0, max(truth_values) * 1.3))
        ax.set_title(f"Autonomous physics discovery — belief revision {belief.revision}")
        ax.legend(loc="upper right")
        return ()

    animation = FuncAnimation(fig, draw, frames=len(belief_history), interval=1100, repeat_delay=1500)
    animation.save(output / "discovery.gif", writer=PillowWriter(fps=1))
    plt.close(fig)
