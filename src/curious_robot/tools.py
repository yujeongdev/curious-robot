"""Typed application tools exposed to deterministic and LLM planners."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

import numpy as np
from pydantic_core import to_jsonable_python

from .belief import update_belief
from .control import PushExecutionResult
from .environment import BeliefEnvironment, EventIntentSink, StepControl, WorldConfig, rollout_candidate
from .planning import (
    candidate_actions,
    task_relevance_for_actions,
)
from .planning import (
    estimate_parameter_uncertainty as belief_uncertainty,
)
from .planning import (
    evaluate_information_gain as score_information_gain,
)
from .planning import (
    propose_experiment as plan_experiment,
)
from .planning import (
    select_next_action as choose_action,
)
from .ports import RobotRealityPort
from .protocol import (
    PhysicalExperimentProtocol,
    PushProtocol,
    configure_push_protocol,
    load_default_protocol,
    protocol_sha256,
)
from .schemas import (
    ActionCandidate,
    ActionDecision,
    ExperimentCandidate,
    ExperimentEvidence,
    ExperimentPlan,
    ExperimentScore,
    JointState,
    MotionSample,
    ParameterEstimate,
    ParameterName,
    PhysicalBelief,
    Pose,
    PredictedOutcome,
    RobotState,
    SceneObservation,
    ToolCallRecord,
    WrenchObservation,
)
from .sysid import estimate_dynamic_friction, estimate_mass, estimate_static_friction
from .trajectory import minimum_jerk_duration_s


@dataclass
class RobotTools:
    """Only this façade is available to the agent; it contains no truth getter."""

    reality: RobotRealityPort
    belief: PhysicalBelief
    config: WorldConfig
    records: list[ToolCallRecord] = field(default_factory=list)
    evidence: list[ExperimentEvidence] = field(default_factory=list)
    belief_world: BeliefEnvironment | None = None
    protocol: PhysicalExperimentProtocol = field(default_factory=load_default_protocol)
    control_gate: StepControl | None = None
    event_sink: EventIntentSink | None = None
    _sequence: int = 0

    def __post_init__(self) -> None:
        privileged = tuple(
            name for name in ("_parameters", "state_0", "model") if hasattr(self.reality, name)
        )
        if privileged:
            names = ", ".join(privileged)
            raise TypeError(
                "RobotTools requires a narrow RobotRealityPort adapter; "
                f"privileged plant attributes exposed: {names}"
            )

    def _record(self, tool: str, arguments: dict, result: dict) -> None:
        self.records.append(
            ToolCallRecord(
                sequence=self._sequence,
                timestamp_s=self.reality.sim_time_s,
                tool=tool,
                arguments=to_jsonable_python(arguments),
                result_summary=to_jsonable_python(result),
            )
        )
        self._sequence += 1

    # Perception
    def observe_scene(self) -> SceneObservation:
        result = SceneObservation(
            object=self.reality.observe_object_pose(),
            target_center_m=self.config.target_position_m,
            target_radius_m=self.config.target_radius_m,
            table_height_m=0.0,
            robot=self.get_robot_state(record=False),
        )
        self._record("observe_scene", {}, {"object_position_m": result.object.pose.position_m})
        return result

    def observe_object_pose(self):
        result = self.reality.observe_object_pose()
        self._record("observe_object_pose", {}, {"position_m": result.pose.position_m})
        return result

    def get_robot_state(self, *, record: bool = True) -> RobotState:
        names, position, velocity = self.reality.joint_state_arrays()
        result = RobotState(
            joints=JointState(
                names=names,
                position_rad=tuple(position),
                velocity_rad_s=tuple(velocity),
                timestamp_s=self.reality.sim_time_s,
            ),
            end_effector_pose=self.reality.observe_tool_pose(),
            gripper_width_m=float(sum(position[7:9])) if len(position) >= 9 else 0.0,
        )
        if record:
            self._record("get_robot_state", {}, {"joint_count": len(position)})
        return result

    def get_tool_wrench(self) -> WrenchObservation:
        result = self.reality.get_tool_wrench()
        self._record("get_tool_wrench", {}, {"force_n": result.force_n, "producer": result.producer})
        return result

    # Robot actions
    def move_joint(self, targets: dict[int, float], duration_s: float = 0.5) -> RobotState:
        self.reality.command_joint_targets(targets, duration_s)
        result = self.get_robot_state(record=False)
        self._record("move_joint", {"targets": targets, "duration_s": duration_s}, {"ok": True})
        return result

    def move_cartesian(self, target: Pose, speed_m_s: float = 0.05) -> Pose:
        start = np.asarray(self.reality.observe_tool_pose().position_m)
        end = np.asarray(target.position_m)
        distance = float(np.linalg.norm(end - start))
        if speed_m_s <= 0 or speed_m_s > 0.15:
            raise ValueError("Cartesian speed must be in (0, 0.15] m/s")
        duration_s = minimum_jerk_duration_s(distance, speed_m_s)
        motion = self.reality.command_cartesian(target.position_m, duration_s)
        achieved = self.reality.observe_tool_pose()
        self._record(
            "move_cartesian",
            {**target.model_dump(), "speed_m_s": speed_m_s},
            {
                "steps": motion.physics_steps,
                "duration_s": motion.duration_s,
                "achieved_position_m": achieved.position_m,
                "residual_m": motion.tracking_error_m,
            },
        )
        return achieved

    def open_gripper(self) -> RobotState:
        self.reality.set_gripper(0.08)
        self._record("open_gripper", {}, {"width_m": 0.08})
        return self.get_robot_state(record=False)

    def close_gripper(self) -> RobotState:
        self.reality.set_gripper(0.0)
        self._record("close_gripper", {}, {"width_m": 0.0})
        return self.get_robot_state(record=False)

    def grasp_object(self) -> bool:
        raise RuntimeError("grasp unavailable: bilateral physical contact has not passed validation")

    def release_object(self) -> bool:
        self.open_gripper()
        self._record("release_object", {}, {"released": True})
        return True

    def execute_safe_push(
        self,
        target_m: tuple[float, float, float],
        max_force_n: float = 12.0,
        *,
        speed_m_s: float | None = None,
        push_height_m: float | None = None,
    ) -> PushExecutionResult:
        if max_force_n > self.config.maximum_tool_force_n:
            raise ValueError("force exceeds safety limit")
        protocol = configure_push_protocol(
            self.protocol.final_push,
            dt_s=self.config.dt_s,
            speed_m_s=speed_m_s or self.protocol.final_push.contact_speed_m_s,
            push_height_m=push_height_m,
            safety_stop_fraction=1.0,
        )
        result = self.reality.execute_push(
            target_m,
            max_force_n,
            protocol=protocol,
        )
        self._record(
            "execute_safe_push",
            {
                "target_m": target_m,
                "speed_m_s": protocol.contact_speed_m_s,
                "push_height_m": protocol.push_height_offset_m,
            },
            {
                "sample_count": len(result.samples),
                "outcome": result.outcome.value,
                "peak_contact_force_n": result.peak_contact_force_n,
            },
        )
        return result

    # Contact control
    def enable_cartesian_compliance(self) -> bool:
        self.reality.enable_compliance()
        self._record("enable_cartesian_compliance", {}, {"enabled": True})
        return True

    def set_desired_force(self, force_n: tuple[float, float, float]) -> bool:
        self.reality.set_desired_force(force_n)
        self._record("set_desired_force", {"force_n": force_n}, {"accepted": True})
        return True

    def check_force_condition(self, axis: int, threshold_n: float) -> bool:
        measured = abs(self.get_tool_wrench().force_n[axis])
        result = measured >= threshold_n
        self._record(
            "check_force_condition",
            {"axis": axis, "threshold_n": threshold_n},
            {"measured_n": measured, "satisfied": result},
        )
        return result

    def release_force_control(self) -> bool:
        self.reality.release_compliance()
        self._record("release_force_control", {}, {"enabled": False})
        return True

    # Experiments
    def weigh_object(self) -> ExperimentEvidence:
        """Return an explicit non-result until bilateral physical grasp is validated."""
        evidence = ExperimentEvidence(
            evidence_id=f"weigh-{uuid.uuid4().hex[:8]}",
            experiment="weigh",
            samples=(),
            metadata={
                "producer": "unavailable",
                "reason": "bilateral finger-contact lift has not passed the physical grasp gate",
            },
            quality_flags=("physical_grasp_not_validated",),
        )
        self.evidence.append(evidence)
        self._record("weigh_object", {}, {"evidence_id": evidence.evidence_id, "samples": 0})
        return evidence

    def _prepare_physical_tool_tip(self, protocol: PushProtocol) -> tuple[float, float, float]:
        if not self.config.include_robot:
            raise RuntimeError("physical experiments require the Franka articulation")
        cube = self.reality.observe_object_pose().pose.position_m
        tool = self.reality.observe_tool_pose().position_m
        self.release_force_control()
        self.close_gripper()
        self.move_cartesian(
            Pose(position_m=(tool[0], tool[1], protocol.safe_height_m)),
            speed_m_s=protocol.approach_speed_m_s,
        )
        self.move_cartesian(
            Pose(
                position_m=(
                    cube[0] - protocol.precontact_offset_m,
                    cube[1] + protocol.lateral_ik_compensation_m,
                    protocol.safe_height_m,
                )
            ),
            speed_m_s=protocol.approach_speed_m_s,
        )
        self.move_cartesian(
            Pose(
                position_m=(
                    cube[0] - protocol.precontact_offset_m,
                    cube[1] + protocol.lateral_ik_compensation_m,
                    cube[2] + protocol.push_height_offset_m,
                )
            ),
            speed_m_s=protocol.contact_speed_m_s,
        )
        return cube

    def _sample_contact_push(
        self,
        *,
        start_x_m: float,
        end_x_m: float,
        y_m: float,
        z_m: float,
        increment_m: float,
        substeps: int,
        stop_after_breakaway: bool,
        protocol: PushProtocol,
        experiment_id: str,
    ) -> list[MotionSample]:
        samples: list[MotionSample] = []
        initial_x = self.reality.observe_object_pose().pose.position_m[0]
        moving_frames = 0
        contact_seen = False
        try:
            self.enable_cartesian_compliance()
            for command_index, target_x in enumerate(
                np.arange(start_x_m, end_x_m + increment_m / 2, increment_m)
            ):
                segment_end_x = min(float(target_x + increment_m), end_x_m)
                for substep_index in range(1, substeps + 1):
                    phase = substep_index / substeps
                    substep_target_x = float(target_x + phase * (segment_end_x - target_x))
                    self.reality.set_cartesian_target((substep_target_x, y_m, z_m))
                    raw_sample = self.reality.step()
                    phase = (
                        "contact"
                        if raw_sample.contact_valid
                        else ("contact_lost" if contact_seen else "approach")
                    )
                    contact_seen = contact_seen or raw_sample.contact_valid
                    sample = raw_sample.model_copy(
                        update={
                            "experiment_id": experiment_id,
                            "command_id": f"{experiment_id}:cartesian-target:{command_index}",
                            "experiment_phase": phase,
                        }
                    )
                    samples.append(sample)
                    horizontal_force = float(np.linalg.norm(sample.measured_force_n[:2]))
                    if horizontal_force > protocol.maximum_horizontal_force_n:
                        self.reality.hold_current_joint_positions(0.1)
                        return samples
                    moved = (
                        abs(sample.position_m[0] - initial_x) > protocol.breakaway_displacement_m
                        and abs(sample.velocity_m_s[0]) > protocol.breakaway_velocity_m_s
                    )
                    moving_frames = moving_frames + 1 if moved else 0
                    if stop_after_breakaway and moving_frames >= protocol.breakaway_consecutive_frames:
                        return samples
            return samples
        finally:
            self.release_force_control()

    def probe_static_friction(self) -> ExperimentEvidence:
        protocol = self.protocol.static_probe
        evidence_id = f"static-{uuid.uuid4().hex[:8]}"
        cube = self._prepare_physical_tool_tip(protocol)
        samples = self._sample_contact_push(
            start_x_m=cube[0] - protocol.precontact_offset_m,
            end_x_m=cube[0] - protocol.precontact_offset_m + protocol.maximum_travel_m,
            y_m=cube[1] + protocol.lateral_ik_compensation_m,
            z_m=cube[2] + protocol.push_height_offset_m,
            increment_m=protocol.command_increment_m,
            substeps=protocol.physics_substeps_per_command,
            stop_after_breakaway=True,
            protocol=protocol,
            experiment_id=evidence_id,
        )
        moved_frames = sum(
            sample.contact_valid
            and abs(sample.position_m[0] - samples[0].position_m[0]) > protocol.breakaway_displacement_m
            and abs(sample.velocity_m_s[0]) > protocol.breakaway_velocity_m_s
            for sample in samples[-protocol.breakaway_consecutive_frames :]
        )
        contact_count = sum(sample.contact_valid for sample in samples)
        contact_coverage = contact_count / max(len(samples), 1)
        quality_flags = []
        if moved_frames < protocol.breakaway_consecutive_frames:
            quality_flags.append("right_censored_no_breakaway")
        if contact_count < protocol.minimum_valid_contact_samples:
            quality_flags.append("insufficient_native_contact_samples")
        if contact_coverage < protocol.minimum_contact_coverage_fraction:
            quality_flags.append("insufficient_contact_coverage")
        evidence = ExperimentEvidence(
            evidence_id=evidence_id,
            experiment="static_friction",
            samples=tuple(samples),
            metadata={
                "motion_threshold_m_s": protocol.breakaway_velocity_m_s,
                "position_increment_m": protocol.command_increment_m,
                "force_model_discrepancy_fraction": protocol.force_model_discrepancy_fraction,
                "producer": "Newton SensorContact Franka end-effector ↔ cube",
                "controller": "Franka IK minimum-jerk joint-target contact ramp",
                "protocol_schema_version": self.protocol.schema_version,
                "protocol_sha256": protocol_sha256(self.protocol),
                "experiment_correlation_id": evidence_id,
                "raw_sample_count": len(samples),
                "accepted_contact_sample_count": contact_count,
                "rejected_noncontact_sample_count": len(samples) - contact_count,
                "contact_coverage_fraction": contact_coverage,
                "minimum_contact_coverage_fraction": protocol.minimum_contact_coverage_fraction,
                "minimum_valid_contact_samples": protocol.minimum_valid_contact_samples,
            },
            quality_flags=tuple(quality_flags),
        )
        self.evidence.append(evidence)
        self._record("probe_static_friction", {}, {"evidence_id": evidence.evidence_id})
        return evidence

    def probe_dynamic_friction(self) -> ExperimentEvidence:
        protocol = self.protocol.dynamic_probe
        evidence_id = f"dynamic-{uuid.uuid4().hex[:8]}"
        cube = self._prepare_physical_tool_tip(protocol)
        samples = self._sample_contact_push(
            start_x_m=cube[0] - protocol.precontact_offset_m,
            end_x_m=cube[0] - protocol.precontact_offset_m + protocol.maximum_travel_m,
            y_m=cube[1] + protocol.lateral_ik_compensation_m,
            z_m=cube[2] + protocol.push_height_offset_m,
            increment_m=protocol.command_increment_m,
            substeps=protocol.physics_substeps_per_command,
            stop_after_breakaway=False,
            protocol=protocol,
            experiment_id=evidence_id,
        )
        contact_count = sum(sample.contact_valid for sample in samples)
        contact_coverage = contact_count / max(len(samples), 1)
        quality_flags = []
        if contact_count < protocol.minimum_valid_contact_samples:
            quality_flags.append("insufficient_native_contact_samples")
        if contact_coverage < protocol.minimum_contact_coverage_fraction:
            quality_flags.append("insufficient_contact_coverage")
        evidence = ExperimentEvidence(
            evidence_id=evidence_id,
            experiment="dynamic_friction",
            samples=tuple(samples),
            metadata={
                "target_velocity_m_s": protocol.contact_speed_m_s,
                "producer": "Newton SensorContact Franka end-effector ↔ cube",
                "controller": "Franka IK minimum-jerk approach plus maintained-contact slide",
                "protocol_schema_version": self.protocol.schema_version,
                "protocol_sha256": protocol_sha256(self.protocol),
                "experiment_correlation_id": evidence_id,
                "raw_sample_count": len(samples),
                "accepted_contact_sample_count": contact_count,
                "rejected_noncontact_sample_count": len(samples) - contact_count,
                "contact_coverage_fraction": contact_coverage,
                "minimum_contact_coverage_fraction": protocol.minimum_contact_coverage_fraction,
                "minimum_valid_contact_samples": protocol.minimum_valid_contact_samples,
            },
            quality_flags=tuple(quality_flags),
        )
        self.evidence.append(evidence)
        self._record("probe_dynamic_friction", {}, {"evidence_id": evidence.evidence_id})
        return evidence

    def return_object_home(self) -> None:
        """Do not teleport or directly actuate the object across experiments."""
        self._record(
            "return_object_home",
            {},
            {"restored": False, "reason": "object reset is forbidden across physical experiments"},
        )

    def estimate_center_of_mass(self) -> ParameterEstimate:
        estimate = ParameterEstimate(
            name=ParameterName.COM_X,
            mean=0.0,
            std=0.005,
            interval95=(-0.015, 0.015),
            physical_bounds=(-0.015, 0.015),
            units="m",
            status="unidentifiable",
            method="not excited by symmetric first-cube task",
            mismatch_notes=("multiple grasp orientations required for identifiability",),
        )
        self._record("estimate_center_of_mass", {}, {"status": estimate.status})
        return estimate

    # Simulation
    def create_belief_simulation(self) -> BeliefEnvironment:
        if self.belief_world is not None:
            self.belief_world.close()
        belief_config = self.config
        self.belief_world = BeliefEnvironment.from_belief(
            self.belief,
            belief_config,
            control_gate=self.control_gate,
            event_sink=self.event_sink,
        )
        self._record("create_belief_simulation", {}, {"revision": self.belief.revision})
        return self.belief_world

    def update_physical_parameters(self, estimates: list[ParameterEstimate]) -> PhysicalBelief:
        for estimate in estimates:
            self.belief = update_belief(self.belief, estimate)
        self.create_belief_simulation()
        self._record("update_physical_parameters", {}, {"revision": self.belief.revision})
        return self.belief

    def simulate_candidate_action(self, candidate: ActionCandidate) -> PredictedOutcome:
        observed_start = self.reality.observe_object_pose().pose.position_m
        rollout_config = replace(self.config, initial_position_m=observed_start)
        outcome = rollout_candidate(
            self.belief,
            candidate,
            rollout_config,
            self.protocol,
            control_gate=self.control_gate,
            event_sink=self.event_sink,
        )
        self._record(
            "simulate_candidate_action",
            {
                **candidate.model_dump(mode="json"),
                "rollout_initial_position_m": observed_start,
                "world_config_id": rollout_config.config_id,
                "experiment_protocol_id": f"physical-experiment:{protocol_sha256(self.protocol)}",
                "belief_revision": self.belief.revision,
            },
            outcome.model_dump(mode="json"),
        )
        return outcome

    def evaluate_prediction_error(
        self,
        predicted_position_m: tuple[float, float, float],
        observed_position_m: tuple[float, float, float],
    ) -> float:
        error = float(np.linalg.norm(np.asarray(predicted_position_m) - np.asarray(observed_position_m)))
        self._record("evaluate_prediction_error", {}, {"error_m": error})
        return error

    # Agent and evaluation
    def estimate_parameter_uncertainty(self) -> dict[str, float]:
        result = belief_uncertainty(self.belief)
        self._record("estimate_parameter_uncertainty", {}, result)
        return result

    def evaluate_information_gain(
        self, candidate: ExperimentCandidate, relevance: dict[ParameterName, float]
    ) -> ExperimentScore:
        result = score_information_gain(candidate, self.belief, relevance)
        self._record("evaluate_information_gain", candidate.model_dump(), result.model_dump())
        return result

    def propose_experiment(self, completed: set[str]) -> ExperimentPlan:
        actions = candidate_actions(self.config.target_position_m)
        result = plan_experiment(
            self.belief,
            task_relevance_for_actions(actions, available_kinds=frozenset({"push"})),
            completed,
        )
        self._record("propose_experiment", {"completed": sorted(completed)}, result.model_dump())
        return result

    def select_next_action(self, outcomes: list[PredictedOutcome]) -> ActionDecision:
        result = choose_action(outcomes)
        self._record("select_next_action", {}, result.model_dump())
        return result

    def verify_task_success(self) -> bool:
        observation = self.reality.observe_object_pose()
        position = np.asarray(observation.pose.position_m)
        target = np.asarray(self.config.target_position_m)
        planar_error = float(np.linalg.norm(position[:2] - target[:2]))
        stable = np.linalg.norm(observation.linear_velocity_m_s) < 0.02
        target_region = bool(planar_error <= self.config.target_radius_m and position[2] >= 0.015)
        success = bool(target_region and stable)
        self._record(
            "verify_task_success",
            {},
            {
                "success": success,
                "target_error_m": planar_error,
                "target_region": target_region,
                "stable": bool(stable),
            },
        )
        return success


__all__ = [
    "RobotTools",
    "estimate_dynamic_friction",
    "estimate_mass",
    "estimate_static_friction",
]
