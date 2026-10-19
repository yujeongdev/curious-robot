"""Typed application tools exposed to deterministic and LLM planners."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace

import numpy as np

from .belief import update_belief
from .environment import BeliefEnvironment, NewtonWorld, WorldConfig, rollout_candidate
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
from .schemas import (
    ActionCandidate,
    ActionDecision,
    ExperimentCandidate,
    ExperimentEvidence,
    ExperimentPlan,
    ExperimentScore,
    JointState,
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


@dataclass
class RobotTools:
    """Only this façade is available to the agent; it contains no truth getter."""

    reality: NewtonWorld
    belief: PhysicalBelief
    config: WorldConfig
    records: list[ToolCallRecord] = field(default_factory=list)
    evidence: list[ExperimentEvidence] = field(default_factory=list)
    belief_world: BeliefEnvironment | None = None
    _sequence: int = 0

    def _record(self, tool: str, arguments: dict, result: dict) -> None:
        self.records.append(
            ToolCallRecord(
                sequence=self._sequence,
                timestamp_s=self.reality.sim_time_s,
                tool=tool,
                arguments=arguments,
                result_summary=result,
            )
        )
        self._sequence += 1

    # Perception
    def observe_scene(self) -> SceneObservation:
        result = SceneObservation(
            object=self.reality.observe_object_pose(),
            target_center_m=self.config.target_position_m,
            target_radius_m=0.035,
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
            end_effector_pose=Pose(position_m=tuple(self.reality._tool_pose)),
            gripper_width_m=0.08,
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
        start = self.reality._tool_pose.copy()
        end = np.asarray(target.position_m)
        distance = float(np.linalg.norm(end - start))
        if speed_m_s <= 0 or speed_m_s > 0.15:
            raise ValueError("Cartesian speed must be in (0, 0.15] m/s")
        steps = max(1, math.ceil(distance / (speed_m_s * self.config.dt_s)))
        self.reality.command_cartesian(target.position_m, steps * self.config.dt_s)
        self._record("move_cartesian", target.model_dump(), {"steps": steps})
        return target

    def open_gripper(self) -> RobotState:
        self.reality.set_gripper(0.08)
        self._record("open_gripper", {}, {"width_m": 0.08})
        return self.get_robot_state(record=False)

    def close_gripper(self) -> RobotState:
        self.reality.set_gripper(0.0)
        self._record("close_gripper", {}, {"width_m": 0.0})
        return self.get_robot_state(record=False)

    def grasp_object(self) -> bool:
        object_position = np.asarray(self.reality.observe_object_pose().pose.position_m)
        self.move_cartesian(Pose(position_m=tuple(object_position + np.array([0.0, 0.0, 0.04]))))
        self.close_gripper()
        self.reality._grasped = True
        self._record("grasp_object", {}, {"bilateral_contact_model": "compliant-force surrogate"})
        return True

    def release_object(self) -> bool:
        self.reality._grasped = False
        self.open_gripper()
        self._record("release_object", {}, {"released": True})
        return True

    def execute_safe_push(self, target_m: tuple[float, float, float], max_force_n: float = 12.0):
        if max_force_n > 15.0:
            raise ValueError("force exceeds safety limit")
        result = self.reality.execute_push(target_m, max_force_n)
        self._record("execute_safe_push", {"target_m": target_m}, {"sample_count": len(result)})
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
        self._return_object_home()
        self.reality.settle(0.25)
        samples = []
        target_z = 0.10
        for _ in range(round(2.0 / self.config.dt_s)):
            pos, _, velocity = self.reality._cube_state()
            force_z = float(np.clip(45.0 * (target_z - pos[2]) - 6.0 * velocity[2], 0.0, 12.0))
            samples.append(self.reality.step((0.0, 0.0, force_z)))
        stable = tuple(s for s in samples[-round(0.6 / self.config.dt_s) :] if abs(s.velocity_m_s[2]) < 0.01)
        evidence = ExperimentEvidence(
            evidence_id=f"weigh-{uuid.uuid4().hex[:8]}",
            experiment="weigh",
            samples=stable,
            metadata={"tare_force_z_n": 0.0, "controller": "vertical spring-damper", "target_z_m": target_z},
            quality_flags=() if len(stable) >= 3 else ("insufficient_quasistatic_samples",),
        )
        # Lower through the same bounded force path; no privileged state reset.
        home = self.config.initial_position_m
        for _ in range(round(1.5 / self.config.dt_s)):
            pos, _, velocity = self.reality._cube_state()
            error_z = home[2] - pos[2]
            if abs(error_z) < 0.003 and abs(velocity[2]) < 0.02:
                break
            force_z = float(np.clip(35.0 * error_z - 5.0 * velocity[2], 0.0, 12.0))
            self.reality.step((0.0, 0.0, force_z))
        self.evidence.append(evidence)
        self._record("weigh_object", {}, {"evidence_id": evidence.evidence_id, "samples": len(stable)})
        return evidence

    def probe_static_friction(self) -> ExperimentEvidence:
        self._return_object_home()
        self.reality.settle(0.25)
        samples = []
        moved_frames = 0
        for force in np.arange(0.0, 8.01, 0.10):
            for _ in range(6):
                s = self.reality.step((float(force), 0.0, 0.0))
                samples.append(s)
                moved_frames = moved_frames + 1 if abs(s.velocity_m_s[0]) >= 0.01 else 0
                if moved_frames >= 3:
                    break
            if moved_frames >= 3:
                break
        evidence = ExperimentEvidence(
            evidence_id=f"static-{uuid.uuid4().hex[:8]}",
            experiment="static_friction",
            samples=tuple(samples),
            metadata={"motion_threshold_m_s": 0.01, "force_increment_n": 0.10},
            quality_flags=() if moved_frames >= 3 else ("right_censored_no_breakaway",),
        )
        self.evidence.append(evidence)
        self._record("probe_static_friction", {}, {"evidence_id": evidence.evidence_id})
        return evidence

    def probe_dynamic_friction(self) -> ExperimentEvidence:
        self._return_object_home()
        self.reality.settle(0.25)
        samples = []
        target_velocity = 0.04
        sliding_established = False
        mass = self.belief.parameters[ParameterName.MASS].mean
        mu_prior = self.belief.parameters[ParameterName.DYNAMIC_FRICTION].mean
        feedforward = mass * 9.81 * mu_prior
        for index in range(round(2.0 / self.config.dt_s)):
            _, _, velocity = self.reality._cube_state()
            sliding_established = sliding_established or velocity[0] > 0.02
            if sliding_established:
                force_x = float(np.clip(feedforward + 30.0 * (target_velocity - velocity[0]), 0.0, 8.0))
            else:
                force_x = float(min(8.0, 0.5 + 4.0 * index * self.config.dt_s))
            samples.append(self.reality.step((force_x, 0.0, 0.0)))
        evidence = ExperimentEvidence(
            evidence_id=f"dynamic-{uuid.uuid4().hex[:8]}",
            experiment="dynamic_friction",
            samples=tuple(samples),
            metadata={"target_velocity_m_s": target_velocity, "controller": "bounded velocity feedback"},
        )
        self.evidence.append(evidence)
        self._record("probe_dynamic_friction", {}, {"evidence_id": evidence.evidence_id})
        self._return_object_home()
        return evidence

    def _return_object_home(self) -> None:
        position = np.asarray(self.reality.observe_object_pose().pose.position_m)
        home = np.asarray(self.config.initial_position_m)
        if np.linalg.norm(position[:2] - home[:2]) > 0.01 or position[2] > home[2] + 0.01:
            self.reality.execute_pick_place(tuple(home), max_force_n=12.0)

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
        belief_config = replace(self.config, include_robot=False, seed=self.config.seed + 1000)
        self.belief_world = BeliefEnvironment.from_belief(self.belief, belief_config)
        self._record("create_belief_simulation", {}, {"revision": self.belief.revision})
        return self.belief_world

    def update_physical_parameters(self, estimates: list[ParameterEstimate]) -> PhysicalBelief:
        for estimate in estimates:
            self.belief = update_belief(self.belief, estimate)
        self.create_belief_simulation()
        self._record("update_physical_parameters", {}, {"revision": self.belief.revision})
        return self.belief

    def simulate_candidate_action(self, candidate: ActionCandidate) -> PredictedOutcome:
        outcome = rollout_candidate(self.belief, candidate, self.config)
        self._record("simulate_candidate_action", candidate.model_dump(), outcome.model_dump())
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
        result = plan_experiment(self.belief, task_relevance_for_actions(actions), completed)
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
        success = bool(planar_error <= 0.03 and bool(stable) and position[2] >= 0.015)
        self._record("verify_task_success", {}, {"success": success, "target_error_m": planar_error})
        return success


__all__ = [
    "RobotTools",
    "estimate_dynamic_friction",
    "estimate_mass",
    "estimate_static_friction",
]
