"""Independent Newton truth/belief environments and bounded actuator primitives."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

import newton
import newton.ik as ik
import numpy as np
import warp as wp
from newton.sensors import SensorContact

from .protocol import PhysicalExperimentProtocol, PushProtocol, protocol_sha256
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
# Known robot/scene geometry, not episode variables learned by SysID.
FRANKA_BASE_POSITION_M = (-0.45, 0.0, 0.0)
FRANKA_READY_JOINT_Q = (0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04)
FRANKA_HAND_TO_TCP_M = 0.1034


class StepControl(Protocol):
    def before_step(self) -> None: ...


class EventIntentSink(Protocol):
    def emit(self, intent: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class PhysicalParameters:
    """Scene-construction parameters. Truth instances stay private to TruthEnvironment."""

    mass_kg: float
    static_friction: float
    dynamic_friction: float
    com_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    restitution: float = 0.0


@dataclass(frozen=True, slots=True)
class WorldConfig:
    device: str = "cuda:0"
    dt_s: float = 1.0 / 240.0
    initial_position_m: tuple[float, float, float] = (-0.12, 0.0, CUBE_EDGE_M / 2)
    target_position_m: tuple[float, float, float] = (0.15, 0.0, CUBE_EDGE_M / 2)
    target_radius_m: float = 0.06
    observation_noise_std: float = 0.0002
    force_noise_std: float = 0.015
    include_robot: bool = True
    seed: int = 0
    arm_position_stiffness: float = 250.0
    arm_position_damping: float = 30.0
    gripper_position_stiffness: float = 100.0
    gripper_position_damping: float = 10.0
    pusher_radius_m: float = 0.012
    pusher_density_kg_m3: float = 100.0
    pusher_friction: float = 0.5
    maximum_tool_force_n: float = 15.0
    contact_sensor_cutoff_hz: float = 10.0
    prediction_residual_tolerance_m: float = 0.03

    @property
    def config_id(self) -> str:
        """Content-address the complete effective world configuration."""
        canonical = json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))
        return f"world-config:{hashlib.sha256(canonical.encode()).hexdigest()}"


def physical_belief_snapshot_id(belief: PhysicalBelief) -> str:
    """Content-address the posterior used to construct a belief world."""
    canonical = belief.model_dump_json(exclude_none=False)
    return f"physical-belief:{hashlib.sha256(canonical.encode()).hexdigest()}"


class NewtonWorld:
    """A small, independently allocated Newton/MuJoCo Warp world.

    Contact is owned by MuJoCo Warp. Distinct dynamic friction is implemented as
    a documented external constitutive correction after sliding onset because
    Newton's ShapeConfig exposes one Coulomb ``mu``.
    """

    def __init__(
        self,
        parameters: PhysicalParameters,
        config: WorldConfig,
        *,
        control_gate: StepControl | None = None,
        event_sink: EventIntentSink | None = None,
        scene: Literal["truth", "belief"] = "truth",
        scenario_id: str | None = None,
        belief_revision: int | None = None,
    ):
        self._parameters = parameters
        self.config = config
        self.rng = np.random.default_rng(config.seed)
        self.sim_time_s = 0.0
        self.last_commanded_force_n = np.zeros(3)
        self.last_control_torque_nm = np.zeros(3)
        self.last_tool_contact_force_n = np.zeros(3)
        self.last_raw_tool_contact_force_n = np.zeros(3)
        self.last_tool_contact_position_m = np.zeros(3)
        self.last_tool_contact_normal_world = np.zeros(3)
        self.last_tool_contact_active = False
        self._sliding = False
        self._grasped = False
        self._compliance_enabled = False
        self._desired_force_n = np.zeros(3)
        self.control_gate = control_gate
        self.event_sink = event_sink
        self.scene = scene
        self.scenario_id = scenario_id
        self.belief_revision = belief_revision
        self.world_instance_id = str(uuid4())
        self.world_config_id = config.config_id
        self.control_trace: list[tuple[float, ...]] = []
        self.state_trace: list[tuple[float, float, float]] = []
        self._recorder = None
        self._record_stride = 4
        self._step_count = 0
        self._tool_pose = np.asarray(config.initial_position_m, dtype=float) + np.array([-0.08, 0.0, 0.03])
        self._build()

    def _build(self) -> None:
        builder = newton.ModelBuilder()
        ik_builder = None
        material = newton.ModelBuilder.ShapeConfig(
            mu=self._parameters.static_friction,
            restitution=self._parameters.restitution,
        )
        if self.config.include_robot:
            asset = newton.utils.download_asset("franka_emika_panda") / "urdf/fr3_franka_hand.urdf"
            builder.add_urdf(
                asset,
                xform=wp.transform(wp.vec3(*FRANKA_BASE_POSITION_M), wp.quat_identity()),
                floating=False,
                enable_self_collisions=False,
                parse_visuals_as_colliders=True,
                joint_damping_mode="passive",
            )
            pusher_body = next(
                index for index, label in enumerate(builder.body_label) if label.endswith("/fr3_hand")
            )
            pusher_shape = builder.add_shape_sphere(
                pusher_body,
                xform=wp.transform(wp.vec3(0.0, 0.0, FRANKA_HAND_TO_TCP_M), wp.quat_identity()),
                radius=self.config.pusher_radius_m,
                cfg=newton.ModelBuilder.ShapeConfig(
                    mu=self.config.pusher_friction,
                    density=self.config.pusher_density_kg_m3,
                ),
                label="fr3_pusher_tip",
            )
            self._pusher_shape = pusher_shape
            ready = np.asarray(FRANKA_READY_JOINT_Q)
            ready[:7] += np.random.default_rng(self.config.seed + 97).uniform(-0.02, 0.02, 7)
            ready = ready.tolist()
            n = min(len(ready), len(builder.joint_q))
            builder.joint_q[:n] = ready[:n]
            builder.joint_target_q[:n] = ready[:n]
            builder.joint_target_ke[: min(7, n)] = [self.config.arm_position_stiffness] * min(7, n)
            builder.joint_target_kd[: min(7, n)] = [self.config.arm_position_damping] * min(7, n)
            if n >= 9:
                builder.joint_target_ke[7:9] = [self.config.gripper_position_stiffness] * 2
                builder.joint_target_kd[7:9] = [self.config.gripper_position_damping] * 2
            ik_builder = copy.deepcopy(builder)
        density = self._parameters.mass_kg / CUBE_EDGE_M**3
        cube_material = newton.ModelBuilder.ShapeConfig(
            mu=self._parameters.static_friction,
            density=density,
            restitution=self._parameters.restitution,
        )
        self.cube_body = builder.add_body(
            xform=wp.transform(wp.vec3(*self.config.initial_position_m), wp.quat_identity()),
            label="unknown_cube",
        )
        cube_shape = builder.add_shape_box(
            self.cube_body,
            hx=CUBE_EDGE_M / 2,
            hy=CUBE_EDGE_M / 2,
            hz=CUBE_EDGE_M / 2,
            cfg=cube_material,
            label="unknown_cube_collision",
        )
        self._cube_shape = cube_shape
        if self.config.include_robot:
            for shape_index in range(cube_shape):
                if shape_index != pusher_shape:
                    builder.add_shape_collision_filter_pair(shape_index, cube_shape)
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
        self.collision_pipeline = newton.CollisionPipeline(
            self.model,
            rigid_contact_max=self.solver.get_max_contact_count(),
            soft_contact_max=0,
            deterministic=True,
        )
        self.contacts = self.collision_pipeline.contacts()
        self.tool_contact_sensor = None
        self.contact_observables = None
        if self.config.include_robot:
            self.tool_contact_sensor = SensorContact(
                self.model,
                sensing_bodies=["fr3/fr3_hand"],
                counterpart_bodies="unknown_cube",
                request_contact_attributes=False,
            )
            self.contact_observables = self.solver.observables(
                kinds=self.tool_contact_sensor.solver_observable_kinds,
                contacts=self.contacts,
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
        for name in (
            "contact_observables",
            "tool_contact_sensor",
            "contacts",
            "collision_pipeline",
            "solver",
            "control",
            "state_0",
            "state_1",
            "model",
        ):
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

    def cube_controller_state(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return exact cube state for deterministic controller feedback.

        This is a controller boundary, not an agent observation. Agent-facing
        code must use :meth:`observe_object_pose`, which applies the configured
        observation model.
        """
        q = self.state_0.body_q.numpy()[self.cube_body]
        qd = self.state_0.body_qd.numpy()[self.cube_body]
        return q[:3].astype(float), q[3:7].astype(float), qd.astype(float)

    def tool_controller_pose(self) -> Pose:
        """Return the deterministic Cartesian controller target pose."""
        return Pose(position_m=tuple(float(value) for value in self._tool_pose))

    def observe_tool_pose(self) -> Pose:
        """Return FK-derived TCP pose as a hardware-equivalent observation."""
        if not hasattr(self, "_ee_index"):
            return self.tool_controller_pose()
        transform = self.state_0.body_q.numpy()[self._ee_index]
        return Pose(
            position_m=tuple(float(value) for value in transform[:3]),
            orientation_xyzw=tuple(float(value) for value in transform[3:7]),
        )

    def set_grasp_active(self, active: bool) -> None:
        """Set the compliant-grasp surrogate state through its controller API."""
        self._grasped = active

    def step(self) -> MotionSample:
        if self.control_gate is not None:
            self.control_gate.before_step()
        self.state_0.clear_forces()
        position, _, velocity = self.cube_controller_state()
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
        body_f = self.state_0.body_f.numpy()
        # This is part of the hidden reality constitutive law, not an agent
        # action.  Robot commands may never write an external cube force.
        body_f[self.cube_body, :3] = correction
        self.state_0.body_f.assign(body_f)
        self.solver.step(
            self.state_0,
            self.state_1,
            self.control,
            self.contacts,
            self.config.dt_s,
            observables=self.contact_observables,
        )
        self.state_0, self.state_1 = self.state_1, self.state_0
        self._update_tool_contact_sensor()
        self.sim_time_s += self.config.dt_s
        pos, _, vel = self.cube_controller_state()
        self.control_trace.append(tuple(float(v) for v in self.control.joint_target_q.numpy()))
        self.state_trace.append(tuple(float(v) for v in pos))
        if self._recorder is not None and self._step_count % self._record_stride == 0:
            self._recorder.begin_frame(self.sim_time_s)
            self._recorder.log_state(self.state_0)
            self._recorder.end_frame()
        self._step_count += 1
        if self.event_sink is not None and self._step_count % 8 == 0:
            self._emit_visual_frame(pos)
        measured = -self.last_tool_contact_force_n + self.rng.normal(0.0, self.config.force_noise_std, 3)
        measured_position = pos + self.rng.normal(0.0, self.config.observation_noise_std, 3)
        return MotionSample(
            timestamp_s=self.sim_time_s,
            position_m=tuple(float(v) for v in measured_position),
            velocity_m_s=tuple(float(v) for v in vel[:3]),
            measured_force_n=tuple(float(v) for v in measured),
            contact_valid=self.last_tool_contact_active,
            contact_pair=("fr3_pusher_tip", "unknown_cube_collision")
            if self.last_tool_contact_active
            else None,
            contact_position_m=tuple(float(v) for v in self.last_tool_contact_position_m)
            if self.last_tool_contact_active
            else None,
            contact_force_producer="Newton SensorContact CONTACT_F"
            if self.last_tool_contact_active
            else None,
            # SolverMuJoCo currently has no validated normal-load producer in
            # this MVP. Never substitute the hidden body mass for a sensor.
            measured_normal_load_n=None,
            normal_load_standard_deviation_n=None,
            normal_load_producer=None,
        )

    def _update_tool_contact_sensor(self) -> None:
        previous_filtered = self.last_tool_contact_force_n.copy()
        self.last_raw_tool_contact_force_n[:] = 0.0
        self.last_tool_contact_position_m[:] = 0.0
        self.last_tool_contact_normal_world[:] = 0.0
        self.last_tool_contact_active = False
        if self.tool_contact_sensor is None:
            return
        self.tool_contact_sensor.update(
            self.state_0,
            self.contacts,
            observables=self.contact_observables,
        )
        force_matrix = self.tool_contact_sensor.force_matrix.numpy()
        position_matrix = self.tool_contact_sensor.position_matrix.numpy()
        forces = force_matrix[:, 0, :]
        raw_force = np.sum(forces, axis=0).astype(float)
        self.last_raw_tool_contact_force_n = raw_force
        time_constant_s = 1.0 / (2.0 * math.pi * self.config.contact_sensor_cutoff_hz)
        alpha = self.config.dt_s / (time_constant_s + self.config.dt_s)
        self.last_tool_contact_force_n = previous_filtered + alpha * (raw_force - previous_filtered)
        weights = np.linalg.norm(forces, axis=1)
        if float(np.sum(weights)) > 1e-9:
            self.last_tool_contact_position_m = np.average(
                position_matrix[:, 0, :], axis=0, weights=weights
            ).astype(float)

        contact_count = int(self.contacts.rigid_contact_count.numpy()[0])
        shape_0 = self.contacts.rigid_contact_shape0.numpy()[:contact_count]
        shape_1 = self.contacts.rigid_contact_shape1.numpy()[:contact_count]
        normals = self.contacts.rigid_contact_normal.numpy()[:contact_count]
        contact_forces = self.contact_observables.contact_f.numpy()[:contact_count]
        oriented_normals: list[np.ndarray] = []
        normal_weights: list[float] = []
        for first, second, normal, contact_force in zip(
            shape_0, shape_1, normals, contact_forces, strict=True
        ):
            if int(first) == self._pusher_shape and int(second) == self._cube_shape:
                oriented = np.asarray(normal, dtype=float)
            elif int(first) == self._cube_shape and int(second) == self._pusher_shape:
                oriented = -np.asarray(normal, dtype=float)
            else:
                continue
            weight = float(np.linalg.norm(contact_force[:3]))
            if weight <= 1e-6:
                continue
            oriented_normals.append(oriented)
            normal_weights.append(weight)

        self.last_tool_contact_active = bool(oriented_normals) and float(np.linalg.norm(raw_force)) > 1e-6
        if self.last_tool_contact_active:
            average_normal = np.average(oriented_normals, axis=0, weights=normal_weights)
            normal_magnitude = float(np.linalg.norm(average_normal))
            if normal_magnitude > 1e-9:
                self.last_tool_contact_normal_world = average_normal / normal_magnitude

    def _emit_visual_frame(self, cube_position: np.ndarray) -> None:
        body_q = self.state_0.body_q.numpy()
        labels = tuple(self.model.body_label)
        bodies = []
        for body_index, transform in enumerate(body_q):
            label = labels[body_index] if body_index < len(labels) else f"body_{body_index}"
            stable_label = label.strip("/").replace("/", "__") or f"body_{body_index}"
            bodies.append(
                {
                    "body_id": stable_label,
                    "label": label,
                    "asset_node_id": stable_label,
                    "position_m": tuple(float(value) for value in transform[:3]),
                    "orientation_xyzw": tuple(float(value) for value in transform[3:7]),
                }
            )
        frame_id = self._step_count // 8
        measured_force = -self.last_tool_contact_force_n
        raw_contact_force = -self.last_raw_tool_contact_force_n
        contact_position = tuple(float(value) for value in self.last_tool_contact_position_m)
        contact_normal = tuple(float(value) for value in self.last_tool_contact_normal_world)
        contacts = (
            (
                {
                    "position_m": contact_position,
                    "normal_world": contact_normal,
                    "force_world_n": tuple(float(value) for value in raw_contact_force),
                    "shape_a_label": "fr3_pusher_tip",
                    "shape_b_label": "unknown_cube_collision",
                },
            )
            if self.last_tool_contact_active
            else ()
        )
        vectors = (
            (
                {
                    "vector_id": f"measured-force-{frame_id}",
                    "kind": "measured_force",
                    "origin_m": contact_position,
                    "vector": tuple(float(value) for value in measured_force),
                    "frame": "world",
                    "units": "N",
                    "label": "10 Hz filtered SensorContact robot-on-cube force",
                },
            )
            if self.last_tool_contact_active
            else ()
        )
        self.event_sink.emit(
            {
                "type": "simulation.visual_frame",
                "sim_time_s": self.sim_time_s,
                "frame_id": frame_id,
                "payload": {
                    "frame_id": frame_id,
                    "source_state_sequence": 0,
                    "scene": self.scene,
                    "world_instance_id": self.world_instance_id,
                    "scenario_id": self.scenario_id,
                    "action_id": self.scenario_id,
                    "belief_revision": self.belief_revision,
                    "body_manifest_id": ("franka-cube-v1" if self.config.include_robot else "cube-only-v1"),
                    "sim_time_s": self.sim_time_s,
                    "presentation_time_s": self.sim_time_s,
                    "bodies": bodies,
                    "contacts": contacts,
                    "vectors": vectors,
                    "target_region": {
                        "center_m": self.config.target_position_m,
                        "radius_m": self.config.target_radius_m,
                        "label": "target",
                    },
                    "camera_hint": {"preset": "tabletop", "target_m": self.config.target_position_m},
                },
            }
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
        position, quat, velocity = self.cube_controller_state()
        noisy = position + self.rng.normal(0.0, self.config.observation_noise_std, 3)
        return ObjectPoseObservation(
            pose=Pose(position_m=tuple(noisy), orientation_xyzw=tuple(quat)),
            linear_velocity_m_s=tuple(velocity[:3]),
            angular_velocity_rad_s=tuple(velocity[3:6]),
            covariance_diagonal=(self.config.observation_noise_std**2,) * 3 + (1e-6,) * 3,
            timestamp_s=self.sim_time_s,
        )

    def get_tool_wrench(self) -> WrenchObservation:
        noisy = -self.last_tool_contact_force_n + self.rng.normal(0.0, self.config.force_noise_std, 3)
        return WrenchObservation(
            force_n=tuple(float(v) for v in noisy),
            torque_nm=tuple(float(v) for v in self.last_control_torque_nm),
            frame="world",
            producer=(
                "Newton SensorContact: cube-on-hand/fingers linear contact force, sign-flipped to "
                f"robot-on-cube with {self.config.contact_sensor_cutoff_hz:g} Hz first-order sensor "
                "filter; excludes internal transmission loads and contact torque"
            ),
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
        if duration_s <= 0.0:
            return
        for _ in range(max(1, round(duration_s / self.config.dt_s))):
            self.step()

    def command_cartesian(self, target_m: tuple[float, float, float], duration_s: float) -> None:
        """Solve Franka IK and execute the result through bounded joint PD targets."""
        self.set_cartesian_target(target_m)
        for _ in range(max(1, round(duration_s / self.config.dt_s))):
            self.step()

    def set_cartesian_target(self, target_m: tuple[float, float, float]) -> None:
        """Set a bounded IK-derived joint target without advancing physics."""
        if not hasattr(self, "_ik_solver"):
            raise RuntimeError("Cartesian robot commands require a Franka articulation")
        current = self.state_0.joint_q.numpy()[: self._ik_model.joint_coord_count]
        self._ik_q.assign(current.reshape(1, -1))
        self._ik_position.set_target_positions(wp.array([target_m], dtype=wp.vec3, device=self.config.device))
        self._ik_solver.step(self._ik_q, self._ik_q, iterations=32)
        solution = self._ik_q.numpy()[0]
        self.command_joint_targets(
            {index: float(solution[index]) for index in range(min(7, len(solution)))},
            0.0,
        )
        self._tool_pose = np.asarray(target_m, dtype=float)

    def set_gripper(self, width_m: float) -> None:
        joint_labels = tuple(self.model.joint_label)
        try:
            left_joint = joint_labels.index("fr3/fr3_finger_joint1")
            right_joint = joint_labels.index("fr3/fr3_finger_joint2")
        except ValueError:
            return
        half = float(np.clip(width_m / 2, 0.0, 0.04))
        joint_q_start = self.model.joint_q_start.numpy()
        self.command_joint_targets(
            {
                int(joint_q_start[left_joint]): half,
                int(joint_q_start[right_joint]): half,
            },
            0.35,
        )

    def enable_compliance(self) -> None:
        self._compliance_enabled = True

    def release_compliance(self) -> None:
        self._compliance_enabled = False
        self._desired_force_n[:] = 0.0

    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        """Reject force commands until measured-feedback control is validated."""
        force = np.asarray(force_n, dtype=float)
        norm = float(np.linalg.norm(force))
        if norm > self.config.maximum_tool_force_n:
            raise ValueError(f"desired force exceeds {self.config.maximum_tool_force_n:g} N safety bound")
        raise RuntimeError(
            "desired-force control unavailable: no validated Cartesian admittance or impedance "
            "controller is connected to measured contact-force feedback"
        )

    def execute_push(
        self,
        target_m: tuple[float, float, float],
        max_force_n: float = 12.0,
        *,
        protocol: PushProtocol,
    ) -> list[MotionSample]:
        if not self.config.include_robot:
            raise RuntimeError("physical push requires the Franka articulation")
        samples: list[MotionSample] = []
        self.set_gripper(0.0)
        for _attempt in range(protocol.maximum_attempts):
            start = np.asarray(self.observe_object_pose().pose.position_m, dtype=float)
            if abs(start[0] - target_m[0]) < 0.012:
                break
            tool = self.observe_tool_pose().position_m
            self.command_cartesian((tool[0], tool[1], protocol.safe_height_m), 0.5)
            push_y = float(start[1] + protocol.lateral_ik_compensation_m)
            start_x = float(start[0] - protocol.precontact_offset_m)
            self.command_cartesian((start_x, push_y, protocol.safe_height_m), 0.5)
            push_z = float(start[2] + protocol.push_height_offset_m)
            self.command_cartesian((start_x, push_y, push_z), 0.5)
            end_x = max(target_m[0], start_x + protocol.maximum_travel_m)
            stop_segment = False
            for target_x in np.arange(start_x, end_x, protocol.command_increment_m):
                self.set_cartesian_target((float(target_x), push_y, push_z))
                for _ in range(protocol.physics_substeps_per_command):
                    sample = self.step()
                    samples.append(sample)
                    if abs(sample.measured_force_n[0]) > protocol.safety_stop_fraction * max_force_n:
                        stop_segment = True
                        break
                reached_target = samples[-1].position_m[0] >= target_m[0] - 0.012
                if stop_segment or reached_target:
                    break
        for _ in range(round(0.35 / self.config.dt_s)):
            samples.append(self.step())
        return samples

    def execute_pick_place(
        self,
        target_m: tuple[float, float, float],
        max_force_n: float = 12.0,
        *,
        assumed_mass_kg: float,
    ) -> list[MotionSample]:
        """Reject pick/place until bilateral physical grasp validation passes."""
        del target_m, max_force_n, assumed_mass_kg
        raise RuntimeError("pick/place unavailable: bilateral physical grasp is not validated")


class TruthEnvironment(NewtonWorld):
    """Hidden-parameter environment; public methods deliberately expose observations only."""


class BeliefEnvironment(NewtonWorld):
    @classmethod
    def from_belief(
        cls,
        belief: PhysicalBelief,
        config: WorldConfig,
        *,
        control_gate: StepControl | None = None,
        event_sink: EventIntentSink | None = None,
        scenario_id: str | None = None,
    ) -> BeliefEnvironment:
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
            control_gate=control_gate,
            event_sink=event_sink,
            scene="belief",
            scenario_id=scenario_id,
            belief_revision=belief.revision,
        )


def rollout_candidate(
    belief: PhysicalBelief,
    candidate: ActionCandidate,
    config: WorldConfig,
    protocol: PhysicalExperimentProtocol,
    *,
    control_gate: StepControl | None = None,
    event_sink: EventIntentSink | None = None,
) -> PredictedOutcome:
    snapshot_id = physical_belief_snapshot_id(belief)
    protocol_id = f"physical-experiment:{protocol_sha256(protocol)}"
    if candidate.kind == "pick_place":
        initial = tuple(float(value) for value in config.initial_position_m)
        error = float(np.linalg.norm(np.asarray(initial[:2]) - np.asarray(candidate.target_m[:2])))
        return PredictedOutcome(
            action_id=candidate.action_id,
            final_position_m=initial,
            target_error_m=error,
            success_probability=0.0,
            tipping_margin=0.0,
            peak_force_n=0.0,
            duration_s=0.0,
            feasible=False,
            rationale="rejected: bilateral physical grasp has not passed validation",
            scenario_id=candidate.action_id,
            world_config_id=config.config_id,
            experiment_protocol_id=protocol_id,
            belief_revision=belief.revision,
            belief_snapshot_id=snapshot_id,
        )
    world = BeliefEnvironment.from_belief(
        belief,
        config,
        control_gate=control_gate,
        event_sink=event_sink,
        scenario_id=candidate.action_id,
    )
    try:
        world.settle()
        samples = world.execute_push(
            candidate.target_m,
            candidate.max_force_n,
            protocol=protocol.final_push,
        )
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
            scenario_id=candidate.action_id,
            world_instance_id=world.world_instance_id,
            world_config_id=world.world_config_id,
            experiment_protocol_id=protocol_id,
            belief_revision=belief.revision,
            belief_snapshot_id=snapshot_id,
        )
    finally:
        world.close()
