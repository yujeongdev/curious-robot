"""Hardware-portable trajectory and bounded push control contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import newton
import numpy as np
import warp as wp

from .schemas import MotionSample
from .trajectory import (
    CartesianMotionResult,
    CartesianTrackingError,
    minimum_jerk_blend,
    minimum_jerk_duration_s,
)

if TYPE_CHECKING:
    from .environment import NewtonWorld
    from .protocol import PushProtocol


class PushExecutionOutcome(StrEnum):
    COMPLETED = "completed"
    SAFETY_STOPPED = "safety_stopped"
    TRAVEL_EXHAUSTED = "travel_exhausted"


@dataclass(frozen=True, slots=True)
class PushExecutionResult:
    samples: tuple[MotionSample, ...]
    outcome: PushExecutionOutcome
    peak_contact_force_n: float

    @property
    def safety_tripped(self) -> bool:
        return self.outcome is PushExecutionOutcome.SAFETY_STOPPED


class NewtonFrankaController:
    """Bounded Franka controller for the Newton/MuJoCo reality plant."""

    def __init__(self, world: NewtonWorld) -> None:
        self._world = world

    def command_joint_targets(self, targets: dict[int, float], duration_s: float = 0.5) -> None:
        world = self._world
        target = world.control.joint_target_q.numpy()
        for index, value in targets.items():
            if not 0 <= index < len(target):
                raise IndexError(f"joint coordinate {index} out of bounds")
            target[index] = value
        world.control.joint_target_q.assign(target)
        if duration_s <= 0.0:
            return
        for _ in range(max(1, round(duration_s / world.config.dt_s))):
            world.step()

    def command_cartesian(
        self,
        target_m: tuple[float, float, float],
        duration_s: float,
    ) -> CartesianMotionResult:
        world = self._world
        target = np.asarray(target_m, dtype=float)
        ik_target = target.copy()
        total_steps = 0
        tracking_error = float("inf")
        for correction_index in range(world.config.maximum_cartesian_corrections + 1):
            solution = self._solve_cartesian_ik(tuple(float(value) for value in ik_target))
            move_duration = duration_s if correction_index == 0 else 0.0
            total_steps += self._execute_joint_solution(solution[:7], move_duration)
            total_steps += self._settle_cartesian_target(target)
            world._tool_pose = np.asarray(world.observe_tool_pose().position_m, dtype=float)
            tracking_delta = target - world._tool_pose
            tracking_error = float(np.linalg.norm(tracking_delta))
            if tracking_error <= world.config.cartesian_tracking_tolerance_m:
                return CartesianMotionResult(
                    physics_steps=total_steps,
                    duration_s=total_steps * world.config.dt_s,
                    tracking_error_m=tracking_error,
                )
            correction_norm = float(np.linalg.norm(tracking_delta))
            correction_scale = min(1.0, world.config.maximum_cartesian_correction_m / correction_norm)
            ik_target = target + tracking_delta * correction_scale
        raise CartesianTrackingError(
            f"Franka tracking error {tracking_error:.6f} m exceeds "
            f"{world.config.cartesian_tracking_tolerance_m:.6f} m after {total_steps} physics steps"
        )

    def command_cartesian_at_speed(
        self,
        target_m: tuple[float, float, float],
        speed_m_s: float,
    ) -> CartesianMotionResult:
        world = self._world
        if speed_m_s > world.config.maximum_cartesian_speed_m_s:
            raise ValueError(
                f"Cartesian speed exceeds {world.config.maximum_cartesian_speed_m_s:g} m/s safety bound"
            )
        start = np.asarray(world.observe_tool_pose().position_m, dtype=float)
        distance = float(np.linalg.norm(np.asarray(target_m, dtype=float) - start))
        return self.command_cartesian(target_m, minimum_jerk_duration_s(distance, speed_m_s))

    def set_cartesian_target(self, target_m: tuple[float, float, float]) -> None:
        world = self._world
        solution = self._solve_cartesian_ik(target_m)
        self.command_joint_targets(
            {index: float(solution[index]) for index in range(min(7, len(solution)))},
            0.0,
        )
        world._tool_pose = np.asarray(target_m, dtype=float)

    def set_gripper(self, width_m: float) -> None:
        world = self._world
        joint_labels = tuple(world.model.joint_label)
        try:
            left_joint = joint_labels.index("fr3/fr3_finger_joint1")
            right_joint = joint_labels.index("fr3/fr3_finger_joint2")
        except ValueError:
            return
        half = float(np.clip(width_m / 2, 0.0, 0.04))
        joint_q_start = world.model.joint_q_start.numpy()
        self.command_joint_targets(
            {
                int(joint_q_start[left_joint]): half,
                int(joint_q_start[right_joint]): half,
            },
            0.35,
        )

    def enable_compliance(self) -> None:
        world = self._world
        self._set_arm_gains(
            world.config.contact_arm_position_stiffness,
            world.config.contact_arm_position_damping,
        )
        world._compliance_enabled = True

    def release_compliance(self) -> None:
        world = self._world
        self._set_arm_gains(
            world.config.arm_position_stiffness,
            world.config.arm_position_damping,
        )
        world._compliance_enabled = False
        world._desired_force_n[:] = 0.0

    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        world = self._world
        force = np.asarray(force_n, dtype=float)
        norm = float(np.linalg.norm(force))
        if norm > world.config.maximum_tool_force_n:
            raise ValueError(f"desired force exceeds {world.config.maximum_tool_force_n:g} N safety bound")
        raise RuntimeError(
            "desired-force control unavailable: no validated Cartesian admittance or impedance "
            "controller is connected to measured contact-force feedback"
        )

    def execute_push(
        self,
        target_m: tuple[float, float, float],
        max_force_n: float,
        *,
        protocol: PushProtocol,
    ) -> PushExecutionResult:
        world = self._world
        if not world.config.include_robot:
            raise RuntimeError("physical push requires the Franka articulation")
        if not math.isfinite(max_force_n) or not 0.0 < max_force_n <= world.config.maximum_tool_force_n:
            raise ValueError(f"max force must be in (0, {world.config.maximum_tool_force_n:g}] N")
        samples: list[MotionSample] = []
        peak_contact_force_n = 0.0
        outcome = PushExecutionOutcome.TRAVEL_EXHAUSTED
        self.release_compliance()
        self.set_gripper(0.0)
        for _attempt in range(protocol.maximum_attempts):
            start = np.asarray(world.observe_object_pose().pose.position_m, dtype=float)
            planar_delta = np.asarray(target_m[:2], dtype=float) - start[:2]
            planar_distance = float(np.linalg.norm(planar_delta))
            if planar_distance <= world.config.target_radius_m:
                outcome = PushExecutionOutcome.COMPLETED
                break
            push_direction = planar_delta / planar_distance
            lateral_direction = np.array((-push_direction[1], push_direction[0]))
            tool = world.observe_tool_pose().position_m
            self.command_cartesian_at_speed(
                (tool[0], tool[1], protocol.safe_height_m), protocol.approach_speed_m_s
            )
            contact_start_xy = (
                start[:2]
                - push_direction * protocol.precontact_offset_m
                + lateral_direction * protocol.lateral_ik_compensation_m
            )
            self.command_cartesian_at_speed(
                (float(contact_start_xy[0]), float(contact_start_xy[1]), protocol.safe_height_m),
                protocol.approach_speed_m_s,
            )
            push_z = float(start[2] + protocol.push_height_offset_m)
            self.command_cartesian_at_speed(
                (float(contact_start_xy[0]), float(contact_start_xy[1]), push_z),
                protocol.contact_speed_m_s,
            )
            outcome, peak_contact_force_n = self._execute_contact_ramp(
                samples,
                contact_start_xy=contact_start_xy,
                push_direction=push_direction,
                push_z=push_z,
                target_xy=np.asarray(target_m[:2], dtype=float),
                max_force_n=max_force_n,
                peak_contact_force_n=peak_contact_force_n,
                protocol=protocol,
            )
            if outcome is not PushExecutionOutcome.TRAVEL_EXHAUSTED:
                break
        for _ in range(round(0.35 / world.config.dt_s)):
            samples.append(world.step())
        final_xy = np.asarray(world.observe_object_pose().pose.position_m[:2], dtype=float)
        if (
            outcome is PushExecutionOutcome.TRAVEL_EXHAUSTED
            and np.linalg.norm(final_xy - np.asarray(target_m[:2], dtype=float))
            <= world.config.target_radius_m
        ):
            outcome = PushExecutionOutcome.COMPLETED
        return PushExecutionResult(
            samples=tuple(samples),
            outcome=outcome,
            peak_contact_force_n=peak_contact_force_n,
        )

    def hold_current_joint_positions(self, duration_s: float) -> None:
        world = self._world
        world.control.joint_target_q.assign(world.state_0.joint_q.numpy())
        for _ in range(max(1, math.ceil(duration_s / world.config.dt_s))):
            world.step()

    def _execute_contact_ramp(
        self,
        samples: list[MotionSample],
        *,
        contact_start_xy: np.ndarray,
        push_direction: np.ndarray,
        push_z: float,
        target_xy: np.ndarray,
        max_force_n: float,
        peak_contact_force_n: float,
        protocol: PushProtocol,
    ) -> tuple[PushExecutionOutcome, float]:
        world = self._world
        outcome = PushExecutionOutcome.TRAVEL_EXHAUSTED
        try:
            self.enable_compliance()
            for travel_m in np.arange(0.0, protocol.maximum_travel_m, protocol.command_increment_m):
                segment_end_m = min(
                    float(travel_m + protocol.command_increment_m),
                    protocol.maximum_travel_m,
                )
                for substep_index in range(1, protocol.physics_substeps_per_command + 1):
                    phase = substep_index / protocol.physics_substeps_per_command
                    substep_travel_m = float(travel_m + phase * (segment_end_m - travel_m))
                    substep_target_xy = contact_start_xy + push_direction * substep_travel_m
                    self.set_cartesian_target(
                        (float(substep_target_xy[0]), float(substep_target_xy[1]), push_z)
                    )
                    sample = world.step()
                    samples.append(sample)
                    contact_force_n = max(
                        float(np.linalg.norm(sample.measured_force_n)),
                        float(np.linalg.norm(world.last_raw_tool_contact_force_n)),
                    )
                    peak_contact_force_n = max(peak_contact_force_n, contact_force_n)
                    if contact_force_n > protocol.safety_stop_fraction * max_force_n:
                        self.hold_current_joint_positions(0.1)
                        return PushExecutionOutcome.SAFETY_STOPPED, peak_contact_force_n
                cube_xy = np.asarray(samples[-1].position_m[:2], dtype=float)
                if np.linalg.norm(cube_xy - target_xy) <= world.config.target_radius_m * 0.5:
                    self._retreat_after_contact(protocol, push_direction)
                    outcome = PushExecutionOutcome.COMPLETED
                    break
            return outcome, peak_contact_force_n
        finally:
            self.release_compliance()

    def _retreat_after_contact(self, protocol: PushProtocol, push_direction: np.ndarray) -> None:
        world = self._world
        self.hold_current_joint_positions(0.05)
        tool = world.observe_tool_pose().position_m
        retreat_distance_m = min(protocol.precontact_offset_m, 0.04)
        retreat_xy = np.asarray(tool[:2], dtype=float) - push_direction * retreat_distance_m
        self.command_cartesian_at_speed(
            (float(retreat_xy[0]), float(retreat_xy[1]), tool[2]),
            min(protocol.contact_speed_m_s, 0.04),
        )

    def _execute_joint_solution(self, solution: np.ndarray, requested_duration_s: float) -> int:
        world = self._world
        start = world.state_0.joint_q.numpy()[:7].astype(float)
        delta = solution - start
        minimum_duration = minimum_jerk_duration_s(
            float(np.max(np.abs(delta))), world.config.maximum_joint_target_speed_rad_s
        )
        step_count = max(1, math.ceil(max(requested_duration_s, minimum_duration) / world.config.dt_s))
        for step_index in range(1, step_count + 1):
            waypoint = start + minimum_jerk_blend(step_index / step_count) * delta
            self.command_joint_targets({index: float(waypoint[index]) for index in range(len(waypoint))}, 0.0)
            world.step()
        return step_count

    def _settle_cartesian_target(self, target: np.ndarray) -> int:
        world = self._world
        settle_steps = math.ceil(world.config.maximum_cartesian_settle_s / world.config.dt_s)
        for step_index in range(settle_steps):
            if (
                np.linalg.norm(np.asarray(world.observe_tool_pose().position_m) - target)
                <= world.config.cartesian_tracking_tolerance_m
            ):
                return step_index
            world.step()
        return settle_steps

    def _solve_cartesian_ik(self, target_m: tuple[float, float, float]) -> np.ndarray:
        world = self._world
        if not hasattr(world, "_ik_solver"):
            raise RuntimeError("Cartesian robot commands require a Franka articulation")
        target = np.asarray(target_m, dtype=float)
        if target.shape != (3,) or not np.all(np.isfinite(target)):
            raise ValueError("Cartesian target must contain three finite coordinates")
        current = world.state_0.joint_q.numpy()[: world._ik_model.joint_coord_count]
        world._ik_q.assign(current.reshape(1, -1))
        world._ik_position.set_target_positions(wp.array([target], dtype=wp.vec3, device=world.config.device))
        world._ik_solver.step(world._ik_q, world._ik_q, iterations=32)
        solution = world._ik_q.numpy()[0]
        world._ik_state.joint_q.assign(solution)
        newton.eval_fk(
            world._ik_model,
            world._ik_state.joint_q,
            world._ik_state.joint_qd,
            world._ik_state,
        )
        achieved = world._ik_state.body_q.numpy()[world._ee_index, :3]
        residual = float(np.linalg.norm(achieved - target))
        if residual > world.config.ik_position_tolerance_m:
            raise RuntimeError(
                f"Franka IK residual {residual:.6f} m exceeds {world.config.ik_position_tolerance_m:.6f} m"
            )
        return solution.astype(float)

    def _set_arm_gains(self, stiffness: float, damping_value: float) -> None:
        world = self._world
        gains = world.model.joint_target_ke.numpy()
        damping = world.model.joint_target_kd.numpy()
        gains[:7] = stiffness
        damping[:7] = damping_value
        world.model.joint_target_ke.assign(gains)
        world.model.joint_target_kd.assign(damping)
        world.solver.notify_model_changed(newton.ModelFlags.JOINT_DOF_FORCE_PROPERTIES)
