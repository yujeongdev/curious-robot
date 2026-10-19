"""Independent Newton truth/belief environments and bounded actuator primitives."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path

import newton
import newton.ik as ik
import numpy as np
import warp as wp

from .schemas import (
    ActionCandidate,
    MotionSample,
    ObjectPoseObservation,
    ParameterName,
    PhysicalBelief,
    Pose,
    PredictedOutcome,
    WrenchObservation,
)

GRAVITY = 9.81
CUBE_EDGE_M = 0.05
TABLE_HEIGHT_M = 0.0


@dataclass(frozen=True, slots=True)
class PhysicalParameters:
    """Scene-construction parameters. Truth instances stay private to TruthEnvironment."""

    mass_kg: float
    static_friction: float
    dynamic_friction: float
    com_m: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class WorldConfig:
    device: str = "cuda:0"
    dt_s: float = 1.0 / 240.0
    initial_position_m: tuple[float, float, float] = (-0.12, 0.0, CUBE_EDGE_M / 2)
    target_position_m: tuple[float, float, float] = (0.15, 0.0, CUBE_EDGE_M / 2)
    observation_noise_std: float = 0.0002
    force_noise_std: float = 0.015
    include_robot: bool = True
    seed: int = 0


class NewtonWorld:
    """A small, independently allocated Newton/MuJoCo Warp world.

    Contact is owned by MuJoCo Warp. Distinct dynamic friction is implemented as
    a documented external constitutive correction after sliding onset because
    Newton's ShapeConfig exposes one Coulomb ``mu``.
    """

    def __init__(self, parameters: PhysicalParameters, config: WorldConfig):
        self._parameters = parameters
        self.config = config
        self.rng = np.random.default_rng(config.seed)
        self.sim_time_s = 0.0
        self.last_applied_force_n = np.zeros(3)
        self.last_control_torque_nm = np.zeros(3)
        self._sliding = False
        self._grasped = False
        self._compliance_enabled = False
        self._desired_force_n = np.zeros(3)
        self.control_trace: list[tuple[float, float, float]] = []
        self.state_trace: list[tuple[float, float, float]] = []
        self._recorder = None
        self._record_stride = 4
        self._step_count = 0
        self._tool_pose = np.asarray(config.initial_position_m, dtype=float) + np.array([-0.08, 0.0, 0.03])
        self._build()

    def _build(self) -> None:
        builder = newton.ModelBuilder()
        ik_builder = None
        material = newton.ModelBuilder.ShapeConfig(mu=self._parameters.static_friction)
        if self.config.include_robot:
            asset = newton.utils.download_asset("franka_emika_panda") / "urdf/fr3_franka_hand.urdf"
            builder.add_urdf(
                asset,
                xform=wp.transform(wp.vec3(-0.45, 0.0, 0.0), wp.quat_identity()),
                floating=False,
                enable_self_collisions=False,
                parse_visuals_as_colliders=True,
                joint_damping_mode="passive",
            )
            ready = np.asarray([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04])
            ready[:7] += np.random.default_rng(self.config.seed + 97).uniform(-0.02, 0.02, 7)
            ready = ready.tolist()
            n = min(len(ready), len(builder.joint_q))
            builder.joint_q[:n] = ready[:n]
            builder.joint_target_q[:n] = ready[:n]
            builder.joint_target_ke[: min(7, n)] = [600.0] * min(7, n)
            builder.joint_target_kd[: min(7, n)] = [50.0] * min(7, n)
            if n >= 9:
                builder.joint_target_ke[7:9] = [100.0, 100.0]
                builder.joint_target_kd[7:9] = [10.0, 10.0]
            ik_builder = copy.deepcopy(builder)
        density = self._parameters.mass_kg / CUBE_EDGE_M**3
        cube_material = newton.ModelBuilder.ShapeConfig(mu=self._parameters.static_friction, density=density)
        self.cube_body = builder.add_body(
            xform=wp.transform(wp.vec3(*self.config.initial_position_m), wp.quat_identity()),
            label="unknown_cube",
        )
        builder.add_shape_box(
            self.cube_body,
            hx=CUBE_EDGE_M / 2,
            hy=CUBE_EDGE_M / 2,
            hz=CUBE_EDGE_M / 2,
            cfg=cube_material,
            label="unknown_cube_collision",
        )
        builder.add_ground_plane(cfg=material)
        self.model = builder.finalize(device=self.config.device)
        self.state_0 = self.model.state()
        self.state_1 = self.model.state()
        self.control = self.model.control()
        newton.eval_fk(self.model, self.model.joint_q, self.model.joint_qd, self.state_0)
        self.solver = newton.solvers.SolverMuJoCo(
            self.model,
            use_mujoco_contacts=True,
            solver="newton",
            integrator="implicitfast",
            cone="elliptic",
            iterations=20,
            ls_iterations=20,
            njmax=512,
            nconmax=256,
            deterministic=wp.DeterministicMode.RUN_TO_RUN,
        )
        self._initial_joint_q = self.model.joint_q.numpy().copy()
        if ik_builder is not None:
            self._setup_ik(ik_builder)

    def _setup_ik(self, builder: newton.ModelBuilder) -> None:
        self._ik_model = builder.finalize(device=self.config.device)
        ik_state = self._ik_model.state()
        newton.eval_fk(self._ik_model, self._ik_model.joint_q, self._ik_model.joint_qd, ik_state)
        self._ee_index = next(
            index for index, label in enumerate(self._ik_model.body_label) if label.endswith("/fr3_hand_tcp")
        )
        ee_transform = ik_state.body_q.numpy()[self._ee_index]
        self._ik_position = ik.IKObjectivePosition(
            link_index=self._ee_index,
            link_offset=wp.vec3(0.0, 0.0, 0.0),
            target_positions=wp.array([ee_transform[:3]], dtype=wp.vec3, device=self.config.device),
        )
        self._ik_rotation = ik.IKObjectiveRotation(
            link_index=self._ee_index,
            link_offset_rotation=wp.quat_identity(),
            target_rotations=wp.array([ee_transform[3:7]], dtype=wp.vec4, device=self.config.device),
        )
        joint_limits = ik.IKObjectiveJointLimit(
            joint_limit_lower=self._ik_model.joint_limit_lower,
            joint_limit_upper=self._ik_model.joint_limit_upper,
        )
        self._ik_q = wp.array(
            self._ik_model.joint_q,
            shape=(1, self._ik_model.joint_coord_count),
            device=self.config.device,
        )
        self._ik_solver = ik.IKSolver(
            model=self._ik_model,
            n_problems=1,
            objectives=[self._ik_position, self._ik_rotation, joint_limits],
            lambda_initial=0.1,
            jacobian_mode=ik.IKJacobianType.ANALYTIC,
        )

    def close(self) -> None:
        """Release large GPU references eagerly between black-box rollouts."""
        self.stop_recording()
        for name in ("solver", "control", "state_0", "state_1", "model"):
            if hasattr(self, name):
                delattr(self, name)

    def reset_cube(self, position_m: tuple[float, float, float] | None = None) -> None:
        position = position_m or self.config.initial_position_m
        for state in (self.state_0, self.state_1):
            q = state.body_q.numpy()
            q[self.cube_body] = (*position, 0.0, 0.0, 0.0, 1.0)
            state.body_q.assign(q)
            qd = state.body_qd.numpy()
            qd[self.cube_body] = 0.0
            state.body_qd.assign(qd)
            state.clear_forces()
        self._sliding = False
        self._grasped = False

    def set_cube_state(
        self,
        body_q: tuple[float, float, float, float, float, float, float],
        body_qd: tuple[float, float, float, float, float, float],
    ) -> None:
        """Restore an explicitly recorded state for deterministic re-execution."""
        for state in (self.state_0, self.state_1):
            q = state.body_q.numpy()
            q[self.cube_body] = body_q
            state.body_q.assign(q)
            qd = state.body_qd.numpy()
            qd[self.cube_body] = body_qd
            state.body_qd.assign(qd)
            state.clear_forces()
        self._sliding = float(np.linalg.norm(body_qd[:2])) > 0.010

    def set_joint_state(
        self,
        joint_q: tuple[float, ...],
        joint_qd: tuple[float, ...],
        joint_target_q: tuple[float, ...],
    ) -> None:
        """Restore robot and free-joint state recorded at an action boundary."""
        for state in (self.state_0, self.state_1):
            state.joint_q.assign(joint_q)
            state.joint_qd.assign(joint_qd)
            newton.eval_fk(self.model, state.joint_q, state.joint_qd, state)
        self.control.joint_target_q.assign(joint_target_q)

    def _cube_state(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        q = self.state_0.body_q.numpy()[self.cube_body]
        qd = self.state_0.body_qd.numpy()[self.cube_body]
        return q[:3].astype(float), q[3:7].astype(float), qd.astype(float)

    def step(self, force_world_n: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> MotionSample:
        self.state_0.clear_forces()
        position, _, velocity = self._cube_state()
        applied = np.asarray(force_world_n, dtype=float)
        planar_speed = float(np.linalg.norm(velocity[:2]))
        if self._sliding:
            self._sliding = planar_speed >= 0.005
        else:
            self._sliding = planar_speed > 0.010
        correction = np.zeros(3)
        if self._sliding and planar_speed > 1e-9:
            correction[:2] = (
                (self._parameters.static_friction - self._parameters.dynamic_friction)
                * self._parameters.mass_kg
                * GRAVITY
                * velocity[:2]
                / planar_speed
            )
        total = applied + correction
        body_f = self.state_0.body_f.numpy()
        body_f[self.cube_body, :3] = total
        self.state_0.body_f.assign(body_f)
        self.solver.step(self.state_0, self.state_1, self.control, None, self.config.dt_s)
        self.state_0, self.state_1 = self.state_1, self.state_0
        self.sim_time_s += self.config.dt_s
        self.last_applied_force_n = applied
        pos, _, vel = self._cube_state()
        self.control_trace.append(tuple(float(v) for v in applied))
        self.state_trace.append(tuple(float(v) for v in pos))
        if self._recorder is not None and self._step_count % self._record_stride == 0:
            self._recorder.begin_frame(self.sim_time_s)
            self._recorder.log_state(self.state_0)
            self._recorder.end_frame()
        self._step_count += 1
        measured = applied + self.rng.normal(0.0, self.config.force_noise_std, 3)
        return MotionSample(
            timestamp_s=self.sim_time_s,
            position_m=tuple(float(v) for v in pos),
            velocity_m_s=tuple(float(v) for v in vel[:3]),
            measured_force_n=tuple(float(v) for v in measured),
            normal_load_n=self._parameters.mass_kg * GRAVITY,
        )

    def start_recording(self, output_path: str | Path, stride: int = 4) -> None:
        self.stop_recording()
        self._record_stride = max(1, stride)
        self._recorder = newton.viewer.ViewerFile(str(output_path), auto_save=False)
        self._recorder.set_model(self.model)

    def stop_recording(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    def clear_trace(self) -> None:
        self.control_trace.clear()
        self.state_trace.clear()

    def settle(self, seconds: float = 0.25) -> None:
        for _ in range(max(1, round(seconds / self.config.dt_s))):
            self.step()

    def observe_object_pose(self) -> ObjectPoseObservation:
        position, quat, velocity = self._cube_state()
        noisy = position + self.rng.normal(0.0, self.config.observation_noise_std, 3)
        return ObjectPoseObservation(
            pose=Pose(position_m=tuple(noisy), orientation_xyzw=tuple(quat)),
            linear_velocity_m_s=tuple(velocity[:3]),
            angular_velocity_rad_s=tuple(velocity[3:6]),
            covariance_diagonal=(self.config.observation_noise_std**2,) * 3 + (1e-6,) * 3,
            timestamp_s=self.sim_time_s,
        )

    def get_tool_wrench(self) -> WrenchObservation:
        noisy = self.last_applied_force_n + self.rng.normal(0.0, self.config.force_noise_std, 3)
        return WrenchObservation(
            force_n=tuple(float(v) for v in noisy),
            torque_nm=tuple(float(v) for v in self.last_control_torque_nm),
            frame="world",
            producer="bounded actuator reaction (virtual F/T sensor; excludes internal transmission loads)",
            timestamp_s=self.sim_time_s,
            standard_deviation_n=self.config.force_noise_std,
        )

    def joint_state_arrays(self) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
        names = tuple(self.model.joint_label)
        return names, self.state_0.joint_q.numpy().astype(float), self.state_0.joint_qd.numpy().astype(float)

    def command_joint_targets(self, targets: dict[int, float], duration_s: float = 0.5) -> None:
        target = self.control.joint_target_q.numpy()
        for index, value in targets.items():
            if not 0 <= index < len(target):
                raise IndexError(f"joint coordinate {index} out of bounds")
            target[index] = value
        self.control.joint_target_q.assign(target)
        for _ in range(max(1, round(duration_s / self.config.dt_s))):
            self.step()

    def command_cartesian(self, target_m: tuple[float, float, float], duration_s: float) -> None:
        """Solve Franka IK and execute the result through bounded joint PD targets."""
        if not hasattr(self, "_ik_solver"):
            for _ in range(max(1, round(duration_s / self.config.dt_s))):
                self.step()
            self._tool_pose = np.asarray(target_m, dtype=float)
            return
        current = self.state_0.joint_q.numpy()[: self._ik_model.joint_coord_count]
        self._ik_q.assign(current.reshape(1, -1))
        self._ik_position.set_target_positions(wp.array([target_m], dtype=wp.vec3, device=self.config.device))
        self._ik_solver.step(self._ik_q, self._ik_q, iterations=32)
        solution = self._ik_q.numpy()[0]
        self.command_joint_targets(
            {index: float(solution[index]) for index in range(min(7, len(solution)))},
            duration_s,
        )
        self._tool_pose = np.asarray(target_m, dtype=float)

    def set_gripper(self, width_m: float) -> None:
        if self.model.joint_coord_count < 2:
            return
        half = float(np.clip(width_m / 2, 0.0, 0.04))
        self.command_joint_targets(
            {self.model.joint_coord_count - 2: half, self.model.joint_coord_count - 1: half}, 0.35
        )

    def enable_compliance(self) -> None:
        self._compliance_enabled = True

    def release_compliance(self) -> None:
        self._compliance_enabled = False
        self._desired_force_n[:] = 0.0

    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        if not self._compliance_enabled:
            raise RuntimeError("compliance must be enabled before setting a force")
        force = np.asarray(force_n, dtype=float)
        norm = float(np.linalg.norm(force))
        if norm > 15.0:
            raise ValueError("desired force exceeds 15 N safety bound")
        self._desired_force_n = force

    def execute_push(
        self, target_m: tuple[float, float, float], max_force_n: float = 12.0
    ) -> list[MotionSample]:
        samples: list[MotionSample] = []
        kp, kd = 30.0, 4.0
        for _ in range(round(4.0 / self.config.dt_s)):
            pos, _, vel = self._cube_state()
            error = np.asarray(target_m[:2]) - pos[:2]
            if float(np.linalg.norm(error)) < 0.008 and float(np.linalg.norm(vel[:2])) < 0.03:
                break
            force = np.zeros(3)
            force[:2] = kp * error - kd * vel[:2]
            magnitude = float(np.linalg.norm(force[:2]))
            if magnitude > max_force_n:
                force[:2] *= max_force_n / magnitude
            samples.append(self.step(tuple(force)))
        for _ in range(round(0.35 / self.config.dt_s)):
            samples.append(self.step())
        return samples

    def execute_pick_place(
        self, target_m: tuple[float, float, float], max_force_n: float = 12.0
    ) -> list[MotionSample]:
        """Physical compliant-grasp surrogate: bounded spring forces, never pose teleportation."""
        self._grasped = True
        samples: list[MotionSample] = []
        lift_z = 0.12
        waypoints = (
            np.array([self._cube_state()[0][0], self._cube_state()[0][1], lift_z]),
            np.array([target_m[0], target_m[1], lift_z]),
            np.array(target_m),
        )
        for waypoint in waypoints:
            for _ in range(round(2.5 / self.config.dt_s)):
                pos, _, vel = self._cube_state()
                error = waypoint - pos
                if float(np.linalg.norm(error)) < 0.006 and float(np.linalg.norm(vel[:3])) < 0.025:
                    break
                force = (
                    38.0 * error - 5.0 * vel[:3] + np.array([0.0, 0.0, self._parameters.mass_kg * GRAVITY])
                )
                magnitude = float(np.linalg.norm(force))
                if magnitude > max_force_n:
                    force *= max_force_n / magnitude
                samples.append(self.step(tuple(force)))
        self._grasped = False
        for _ in range(round(0.4 / self.config.dt_s)):
            samples.append(self.step())
        return samples


class TruthEnvironment(NewtonWorld):
    """Hidden-parameter environment; public methods deliberately expose observations only."""


class BeliefEnvironment(NewtonWorld):
    @classmethod
    def from_belief(cls, belief: PhysicalBelief, config: WorldConfig) -> BeliefEnvironment:
        return cls(
            PhysicalParameters(
                mass_kg=belief.parameters[ParameterName.MASS].mean,
                static_friction=belief.parameters[ParameterName.STATIC_FRICTION].mean,
                dynamic_friction=belief.parameters[ParameterName.DYNAMIC_FRICTION].mean,
                com_m=(
                    belief.parameters[ParameterName.COM_X].mean,
                    belief.parameters[ParameterName.COM_Y].mean,
                    belief.parameters[ParameterName.COM_Z].mean,
                ),
            ),
            config,
        )


def rollout_candidate(
    belief: PhysicalBelief, candidate: ActionCandidate, config: WorldConfig
) -> PredictedOutcome:
    rollout_config = WorldConfig(
        device=config.device,
        dt_s=config.dt_s,
        initial_position_m=config.initial_position_m,
        target_position_m=config.target_position_m,
        observation_noise_std=0.0,
        force_noise_std=0.0,
        include_robot=False,
        seed=config.seed,
    )
    world = BeliefEnvironment.from_belief(belief, rollout_config)
    try:
        world.settle()
        if candidate.kind == "push":
            samples = world.execute_push(candidate.target_m, candidate.max_force_n)
        else:
            samples = world.execute_pick_place(candidate.target_m, candidate.max_force_n)
        observation = world.observe_object_pose()
        final = np.asarray(observation.pose.position_m)
        error = float(np.linalg.norm(final[:2] - np.asarray(candidate.target_m[:2])))
        peak = max((np.linalg.norm(s.measured_force_n) for s in samples), default=0.0)
        quat = observation.pose.orientation_xyzw
        upright_cos = 1.0 - 2.0 * (quat[0] ** 2 + quat[1] ** 2)
        tilt = math.acos(float(np.clip(upright_cos, -1.0, 1.0)))
        feasible = peak <= candidate.max_force_n * 1.01 and tilt < math.radians(15)
        success_probability = float(np.clip(1.0 - error / 0.10 - tilt / math.pi, 0.0, 1.0))
        return PredictedOutcome(
            action_id=candidate.action_id,
            final_position_m=tuple(float(v) for v in final),
            target_error_m=error,
            success_probability=success_probability,
            tipping_margin=math.radians(15) - tilt,
            peak_force_n=float(peak),
            duration_s=len(samples) * config.dt_s,
            feasible=feasible,
            rationale="independent Newton belief rollout with bounded force controller",
        )
    finally:
        world.close()
