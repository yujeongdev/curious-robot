"""Autonomous Observe→Experiment→Identify→Predict→Act→Verify episode runner."""

from __future__ import annotations

import csv
import importlib.metadata
import json
import platform
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import matplotlib
import numpy as np
import warp as wp
from matplotlib.animation import FuncAnimation, PillowWriter

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from .belief import initial_belief
from .environment import PhysicalParameters, StepControl, TruthEnvironment, WorldConfig
from .events import DiscoveryStage
from .planner_adapters import DeterministicPlannerAdapter, PlannerAdapter
from .planning import action_utility, candidate_actions
from .schemas import ActionResult, EpisodeResult, ParameterName, PhysicalBelief
from .surface import SurfaceFamily, SurfaceProfile, sample_surface_profile
from .sysid import (
    calibrate_coulomb_surrogate,
    estimate_dynamic_friction,
    estimate_mass,
    estimate_static_friction,
)
from .tools import RobotTools


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


def run_episode(
    *,
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
    rng = np.random.default_rng(seed)
    bounds = material_bounds or {}
    mass_range = bounds.get("mass_kg", (0.08, 0.45))
    if not isinstance(mass_range, (list, tuple)) or len(mass_range) != 2:
        raise ValueError("material_bounds.mass_kg must be [minimum, maximum]")
    mass_low, mass_high = (float(value) for value in mass_range)
    if not 0.02 <= mass_low <= mass_high <= 2.0:
        raise ValueError("material_bounds.mass_kg must satisfy 0.02 <= min <= max <= 2.0")
    family_value = bounds.get("surface_family")
    surface_family = SurfaceFamily(str(family_value)) if family_value else None
    mass = float(rng.uniform(mass_low, mass_high))
    surface = sample_surface_profile(seed + 1_000, surface_family)
    initial = (float(rng.uniform(-0.16, -0.10)), float(rng.uniform(-0.03, 0.03)), 0.025)
    truth = PhysicalParameters(
        mass,
        surface.effective_static_friction,
        surface.effective_dynamic_friction,
        restitution=surface.restitution,
    )
    config = WorldConfig(
        device=device,
        initial_position_m=initial,
        include_robot=include_robot,
        seed=seed,
    )
    episode_events = _EpisodeEvents(event_sink)
    reality = TruthEnvironment(
        truth,
        config,
        control_gate=control_gate,
        event_sink=event_sink,
        scene="truth",
        scenario_id="simulated-reality",
    )
    tools = RobotTools(reality, initial_belief(), config)
    planner = planner or DeterministicPlannerAdapter()
    belief_history: list[PhysicalBelief] = [tools.belief]
    completed: set[str] = set()
    try:
        _log("OBSERVE", "capturing robot, object, and target state")
        tools.observe_scene()
        experiment_iteration = 0
        for _ in range(4):
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
                estimate = estimate_mass(tools.weigh_object())
            elif selected == "static_breakaway_ramp":
                estimate = estimate_static_friction(
                    tools.probe_static_friction(), tools.belief.parameters[ParameterName.MASS]
                )
            elif selected == "dynamic_controlled_slide":
                estimate = estimate_dynamic_friction(
                    tools.probe_dynamic_friction(),
                    tools.belief.parameters[ParameterName.MASS],
                    tools.belief.parameters[ParameterName.STATIC_FRICTION],
                )
            else:
                estimate = tools.estimate_center_of_mass()
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
                sample_count = min(40, len(evidence.samples))
                sample_indices = np.linspace(0, len(evidence.samples) - 1, sample_count, dtype=int)
                for sample_index in dict.fromkeys(int(index) for index in sample_indices):
                    sample = evidence.samples[sample_index]
                    measured_force = float(np.linalg.norm(sample.measured_force_n))
                    episode_events.emit(
                        "measurement.sampled",
                        {
                            "evidence_id": evidence.evidence_id,
                            "sensor_kind": "virtual_tool_wrench_and_pose",
                            "producer": "bounded actuator reaction plus configured sensor noise",
                            "frame": "world",
                            "units": "SI",
                            "values": {
                                "measured_force_n": measured_force,
                                "object_position_m": float(sample.position_m[0]),
                                "object_velocity_mps": float(sample.velocity_m_s[0]),
                            },
                            "valid": True,
                        },
                        sim_time_s=sample.timestamp_s,
                    )
                episode_events.emit(
                    "measurement.summary",
                    {
                        "evidence_id": evidence.evidence_id,
                        "accepted_count": len(evidence.samples),
                        "rejected_count": 0,
                        "aggregate_values": {
                            "median_force_n": float(
                                np.median(
                                    [np.linalg.norm(sample.measured_force_n) for sample in evidence.samples]
                                )
                            )
                        },
                        "quality_flags": list(evidence.quality_flags),
                    },
                )
            episode_events.enter(DiscoveryStage.SYSTEM_IDENTIFICATION)
            prior_estimate = tools.belief.parameters[estimate.name]
            tools.update_physical_parameters([estimate])
            belief_history.append(tools.belief)
            episode_events.emit(
                "belief.updated",
                {
                    "revision": tools.belief.revision,
                    "parameter_name": estimate.name.value,
                    "prior": _distribution(prior_estimate),
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
                        "body_manifest_id": "cube-only-v1",
                        "parameter_names": [parameter.value for parameter in ParameterName],
                    },
                )
            _log(
                "LEARN",
                f"{estimate.name.value}={estimate.mean:.4f} ± {1.96 * estimate.std:.4f}; "
                f"belief revision={tools.belief.revision}",
            )
            experiment_iteration += 1

        episode_events.enter(DiscoveryStage.PREDICT, iteration=experiment_iteration)
        _log("PREDICT", "rolling out push and pick-and-place in the independent belief simulation")
        actions = candidate_actions(config.target_position_m)
        outcomes = [tools.simulate_candidate_action(action) for action in actions]
        for outcome in outcomes:
            episode_events.emit(
                "prediction.completed",
                {
                    "action_id": outcome.action_id,
                    "scenario_id": outcome.action_id,
                    "final_position_m": outcome.final_position_m,
                    "target_error_m": outcome.target_error_m,
                    "success_probability": outcome.success_probability,
                    "peak_force_n": outcome.peak_force_n,
                    "tipping_margin": outcome.tipping_margin,
                    "feasible": outcome.feasible,
                    "reasons": [outcome.rationale],
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

        tools.return_object_home()
        reality.settle(0.25)
        if selected_action.kind == "pick_place":
            tools.grasp_object()
        initial_position, initial_orientation, initial_velocity = reality.cube_controller_state()
        initial_final_pose = tuple(float(v) for v in initial_position)
        initial_body_q = (*initial_final_pose, *(float(v) for v in initial_orientation))
        initial_body_qd = tuple(float(v) for v in initial_velocity)
        initial_joint_q = tuple(float(v) for v in reality.state_0.joint_q.numpy())
        initial_joint_qd = tuple(float(v) for v in reality.state_0.joint_qd.numpy())
        initial_joint_target_q = tuple(float(v) for v in reality.control.joint_target_q.numpy())
        reality.clear_trace()
        episode_events.enter(DiscoveryStage.EXECUTE)
        replay_path = output / "scene_replay.bin"
        reality.start_recording(replay_path, stride=4)
        if selected_action.kind == "push":
            final_samples = tools.execute_safe_push(selected_action.target_m, selected_action.max_force_n)
        else:
            final_samples = reality.execute_pick_place(
                selected_action.target_m,
                selected_action.max_force_n,
                assumed_mass_kg=tools.belief.parameters[ParameterName.MASS].mean,
            )
        reality.stop_recording()
        final_control_trace = list(reality.control_trace)
        final_state_trace = list(reality.state_trace)
        if selected_action.kind == "pick_place":
            tools.release_object()
        observed = reality.observe_object_pose().pose.position_m
        episode_events.emit(
            "action.executed",
            {
                "action_id": selected_action.action_id,
                "controller_result": "completed",
                "observed_terminal_position_m": observed,
                "safety_event_ids": [],
            },
        )
        predicted = next(o.final_position_m for o in outcomes if o.action_id == selected_action.action_id)
        prediction_error = tools.evaluate_prediction_error(predicted, observed)
        episode_events.enter(DiscoveryStage.VERIFY)
        success = tools.verify_task_success()
        episode_events.emit(
            "verification.completed",
            {
                "success": success,
                "goal_predicates": {"target_region": success, "stable": success},
                "prediction_residual_m": prediction_error,
                "tolerance_m": 0.03,
            },
        )
        _log(
            "VERIFY",
            f"success={success}, prediction_error={prediction_error:.4f} m, observed_position={observed}",
        )
        episode_events.enter(DiscoveryStage.LEARN)
        if prediction_error > 0.03:
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
                        f"final action prediction residual {prediction_error:.4f} m exceeded 0.03 m",
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
            prediction_error_m=prediction_error,
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
            final_samples,
        )
        episode_events.finish()
        return result
    finally:
        if tools.belief_world is not None:
            tools.belief_world.close()
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
    final_control_trace: list[tuple[float, float, float]],
    final_state_trace: list[tuple[float, float, float]],
    final_samples,
) -> None:
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
                "force_x_n",
                "measured_normal_n",
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
                        sample.measured_force_n[0],
                        sample.measured_normal_load_n,
                    ]
                )
    evaluator_truth = asdict(truth)
    evaluator_truth["surface_profile"] = asdict(surface)
    manifest = {
        "schema_version": 1,
        "seed": config.seed,
        "metadata": metadata,
        "world_config": {
            "device": config.device,
            "dt_s": config.dt_s,
            "target_position_m": config.target_position_m,
            "include_robot": config.include_robot,
            "solver_iterations": 20,
            "nconmax": 256,
            "njmax": 512,
        },
        "evaluation_truth": evaluator_truth,
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
        "control_sequence_force_world_n": final_control_trace,
        "reference_trajectory_position_m": final_state_trace,
        "position_tolerance_m": 0.015 if config.include_robot else 0.005,
        "agent_tool_calls": [record.model_dump(mode="json") for record in tools.records],
        "belief_history": [belief.model_dump(mode="json") for belief in belief_history],
        "sensor_configuration": {
            "pose_noise_std_m": config.observation_noise_std,
            "force_noise_std_n": config.force_noise_std,
            "force_producer": "bounded actuator reaction",
        },
    }
    (output / "reexecution.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
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
