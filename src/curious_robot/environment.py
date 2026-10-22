"""Independent Newton truth/belief environments and bounded actuator primitives."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

import newton
import newton.ik as ik
import numpy as np
import warp as wp
from newton.sensors import SensorContact

from .control import (
    NewtonFrankaController,
    PushExecutionResult,
)
from .objects import ContactSurface, get_object_archetype
from .protocol import PhysicalExperimentProtocol, PushProtocol, configure_push_protocol, protocol_sha256
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
from .tasks import PlanarPose, evaluate_planar_goal, get_task_preset, yaw_from_quaternion_xyzw
from .trajectory import CartesianMotionResult
from .world_config import (
    FRANKA_BASE_POSITION_M,
    FRANKA_HAND_TO_TCP_M,
    FRANKA_READY_JOINT_Q,
    GRAVITY,
    PhysicalParameters,
    WorldConfig,
    compound_box_inertia,
    physical_belief_snapshot_id,
    resolved_object_geometry,
)


class StepControl(Protocol):
    def before_step(self) -> None: ...


class EventIntentSink(Protocol):
    def emit(self, intent: dict[str, Any]) -> None: ...


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
        self.last_tool_contact_shape_label: str | None = None
        self.last_object_contact_shape_label: str | None = None
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
        self.orientation_trace: list[tuple[float, float, float, float]] = []
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
                parse_visuals_as_colliders=False,
                joint_damping_mode="passive",
            )
            hand_body = next(
                index for index, label in enumerate(builder.body_label) if label.endswith("/fr3_hand")
            )
            tool_tip_shape = builder.add_shape_sphere(
                hand_body,
                xform=wp.transform(wp.vec3(0.0, 0.0, FRANKA_HAND_TO_TCP_M), wp.quat_identity()),
                radius=self.config.tool_tip_radius_m,
                cfg=newton.ModelBuilder.ShapeConfig(
                    mu=self.config.tool_tip_friction,
                    density=self.config.tool_tip_density_kg_m3,
                ),
                label="fr3_tool_tip_collision",
            )
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
        geometry = resolved_object_geometry(self.config)
        inertia = compound_box_inertia(
            geometry,
            self._parameters.mass_kg,
            self._parameters.com_m,
        )
        object_material = newton.ModelBuilder.ShapeConfig(
            mu=self._parameters.static_friction,
            density=0.0,
            restitution=self._parameters.restitution,
        )
        self.object_body = builder.add_body(
            xform=wp.transform(
                wp.vec3(*self.config.initial_position_m),
                wp.quat(
                    0.0,
                    0.0,
                    math.sin(self.config.initial_yaw_rad / 2.0),
                    math.cos(self.config.initial_yaw_rad / 2.0),
                ),
            ),
            com=wp.vec3(*self._parameters.com_m),
            inertia=wp.mat33(*(value for row in inertia for value in row)),
            mass=self._parameters.mass_kg,
            lock_inertia=True,
            label=geometry.body_label,
        )
        object_shapes = []
        for part in geometry.parts:
            object_shapes.append(
                builder.add_shape_box(
                    self.object_body,
                    xform=wp.transform(wp.vec3(*part.center_m), wp.quat_identity()),
                    hx=part.size_m[0] / 2.0,
                    hy=part.size_m[1] / 2.0,
                    hz=part.size_m[2] / 2.0,
                    cfg=object_material,
                    label=part.label,
                )
            )
        self.object_geometry = geometry
        self.object_shape_indices = frozenset(object_shapes)
        # Compatibility aliases keep the existing cube-oriented controller and
        # deterministic replay APIs stable while object-neutral ports migrate.
        self.cube_body = self.object_body
        self._cube_shape = object_shapes[0]
        if self.config.include_robot:
            end_effector_bodies = {
                index
                for index, label in enumerate(builder.body_label)
                if label.endswith(("/fr3_hand", "/fr3_leftfinger", "/fr3_rightfinger"))
            }
            self._tool_shape_indices = frozenset(
                index
                for index in range(object_shapes[0])
                if index == tool_tip_shape or int(builder.shape_body[index]) in end_effector_bodies
            )
            for shape_index in range(object_shapes[0]):
                if shape_index not in self._tool_shape_indices:
                    for object_shape in object_shapes:
                        builder.add_shape_collision_filter_pair(shape_index, object_shape)
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
                sensing_bodies=["fr3/fr3_hand", "fr3/fr3_leftfinger", "fr3/fr3_rightfinger"],
                counterpart_bodies=geometry.body_label,
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
        self._ik_state = self._ik_model.state()
        self._tool_pose = np.asarray(self.observe_tool_pose().position_m, dtype=float)
        self._franka_controller = NewtonFrankaController(self)

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
        yaw = self.config.initial_yaw_rad
        for state in (self.state_0, self.state_1):
            q = state.body_q.numpy()
            q[self.cube_body] = (*position, 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))
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

    def object_contact_surface(self, surface_id: str) -> ContactSurface:
        """Resolve a public contact face from the task-bound object archetype."""

        preset = get_task_preset(self.config.task_id)
        archetype = get_object_archetype(
            preset.object_archetype_id,
            preset.object_archetype_version,
        )
        return archetype.contact_surface(surface_id)

    def validate_contact_operating_point(
        self,
        *,
        surface_id: str,
        lateral_offset_m: float,
        push_height_object_m: float,
    ) -> ContactSurface:
        """Fail closed before motion when a push point is outside public geometry."""

        preset = get_task_preset(self.config.task_id)
        archetype = get_object_archetype(
            preset.object_archetype_id,
            preset.object_archetype_version,
        )
        return archetype.validate_contact_operating_point(
            surface_id=surface_id,
            lateral_offset_m=lateral_offset_m,
            push_height_object_m=push_height_object_m,
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
        pos, orientation, vel = self.cube_controller_state()
        self.control_trace.append(tuple(float(v) for v in self.control.joint_target_q.numpy()))
        self.state_trace.append(tuple(float(v) for v in pos))
        self.orientation_trace.append(tuple(float(v) for v in orientation))
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
            orientation_xyzw=tuple(float(v) for v in orientation),
            angular_velocity_rad_s=tuple(float(v) for v in vel[3:6]),
            measured_force_n=tuple(float(v) for v in measured),
            contact_valid=self.last_tool_contact_active,
            contact_pair=(
                self.last_tool_contact_shape_label,
                self.last_object_contact_shape_label or self.model.shape_label[self._cube_shape],
            )
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
        self.last_tool_contact_shape_label = None
        self.last_object_contact_shape_label = None
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
        contact_shape_labels: list[str] = []
        object_shape_labels: list[str] = []
        for first, second, normal, contact_force in zip(
            shape_0, shape_1, normals, contact_forces, strict=True
        ):
            if int(first) in self._tool_shape_indices and int(second) in self.object_shape_indices:
                oriented = np.asarray(normal, dtype=float)
                object_shape = int(second)
            elif int(first) in self.object_shape_indices and int(second) in self._tool_shape_indices:
                oriented = -np.asarray(normal, dtype=float)
                object_shape = int(first)
            else:
                continue
            weight = float(np.linalg.norm(contact_force[:3]))
            if weight <= 1e-6:
                continue
            oriented_normals.append(oriented)
            normal_weights.append(weight)
            contact_shape_labels.append(self._shape_contact_label(int(first), int(second)))
            object_shape_labels.append(self.model.shape_label[object_shape])

        self.last_tool_contact_active = bool(oriented_normals) and float(np.linalg.norm(raw_force)) > 1e-6
        if self.last_tool_contact_active:
            self.last_tool_contact_shape_label = contact_shape_labels[int(np.argmax(normal_weights))]
            self.last_object_contact_shape_label = object_shape_labels[int(np.argmax(normal_weights))]
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
                    "shape_a_label": self.last_tool_contact_shape_label,
                    "shape_b_label": (
                        self.last_object_contact_shape_label
                        or self.model.shape_label[self._cube_shape]
                    ),
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
                    "label": "10 Hz filtered SensorContact robot-on-object force",
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
                    "body_manifest_id": (
                        f"franka-{self.object_geometry.kind.replace('_', '-')}-v1"
                        if self.config.include_robot
                        else f"{self.object_geometry.kind.replace('_', '-')}-only-v1"
                    ),
                    "object_geometry": {
                        "archetype_id": self.object_geometry.archetype_id,
                        "archetype_version": self.object_geometry.archetype_version,
                        "geometry_hash": self.object_geometry.geometry_hash,
                        "body_id": self.object_geometry.body_label,
                        "parts": [
                            {
                                "part_id": part.part_id,
                                "center_object_m": part.center_m,
                                "half_extents_m": tuple(value / 2.0 for value in part.size_m),
                                "orientation_object_xyzw": (0.0, 0.0, 0.0, 1.0),
                            }
                            for part in self.object_geometry.parts
                        ],
                    },
                    "tool_tip_radius_m": (
                        self.config.tool_tip_radius_m if self.config.include_robot else None
                    ),
                    "sim_time_s": self.sim_time_s,
                    "presentation_time_s": self.sim_time_s,
                    "bodies": bodies,
                    "contacts": contacts,
                    "vectors": vectors,
                    "target_region": {
                        "center_m": self.config.target_position_m,
                        "radius_m": self.config.target_radius_m,
                        "yaw_rad": self.config.target_yaw_rad,
                        "yaw_tolerance_rad": self.config.target_yaw_tolerance_rad,
                        "label": "target",
                    },
                    "camera_hint": {"preset": "tabletop", "target_m": self.config.target_position_m},
                },
            }
        )

    def _shape_contact_label(self, first_shape: int, second_shape: int) -> str:
        shape_index = first_shape if first_shape in self._tool_shape_indices else second_shape
        label = self.model.shape_label[shape_index]
        if label == "fr3_tool_tip_collision":
            return label
        body_index = int(self.model.shape_body.numpy()[shape_index])
        body_label = self.model.body_label[body_index].rsplit("/", 1)[-1]
        return f"{body_label}_collision"

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
        self.orientation_trace.clear()

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
                "Newton SensorContact: object-on-hand/fingers/tool linear contact force, sign-flipped to "
                f"robot-on-object with {self.config.contact_sensor_cutoff_hz:g} Hz first-order sensor "
                "filter; excludes internal transmission loads and contact torque"
            ),
            timestamp_s=self.sim_time_s,
            standard_deviation_n=self.config.force_noise_std,
        )

    def joint_state_arrays(self) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
        names = tuple(self.model.joint_label)
        return names, self.state_0.joint_q.numpy().astype(float), self.state_0.joint_qd.numpy().astype(float)

    def command_joint_targets(self, targets: dict[int, float], duration_s: float = 0.5) -> None:
        self._franka_controller.command_joint_targets(targets, duration_s)

    def command_cartesian(
        self,
        target_m: tuple[float, float, float],
        duration_s: float,
    ) -> CartesianMotionResult:
        return self._franka_controller.command_cartesian(target_m, duration_s)

    def command_cartesian_at_speed(
        self,
        target_m: tuple[float, float, float],
        speed_m_s: float,
    ) -> CartesianMotionResult:
        return self._franka_controller.command_cartesian_at_speed(target_m, speed_m_s)

    def set_cartesian_target(self, target_m: tuple[float, float, float]) -> None:
        self._franka_controller.set_cartesian_target(target_m)

    def set_gripper(self, width_m: float) -> None:
        self._franka_controller.set_gripper(width_m)

    def enable_compliance(self) -> None:
        self._franka_controller.enable_compliance()

    def release_compliance(self) -> None:
        self._franka_controller.release_compliance()

    def set_desired_force(self, force_n: tuple[float, float, float]) -> None:
        self._franka_controller.set_desired_force(force_n)

    def execute_push(
        self,
        target_m: tuple[float, float, float],
        max_force_n: float = 12.0,
        *,
        protocol: PushProtocol,
    ) -> PushExecutionResult:
        return self._franka_controller.execute_push(target_m, max_force_n, protocol=protocol)

    def hold_current_joint_positions(self, duration_s: float) -> None:
        self._franka_controller.hold_current_joint_positions(duration_s)

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
            final_orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
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
        return rollout_candidate_in_world(
            world,
            belief,
            candidate,
            config,
            protocol,
            belief_snapshot_id=snapshot_id,
            experiment_protocol_id=protocol_id,
        )
    finally:
        world.close()


def rollout_candidate_in_world(
    world: BeliefEnvironment,
    belief: PhysicalBelief,
    candidate: ActionCandidate,
    config: WorldConfig,
    protocol: PhysicalExperimentProtocol,
    *,
    belief_snapshot_id: str | None = None,
    experiment_protocol_id: str | None = None,
) -> PredictedOutcome:
    """Validate a push in an already constructed, independently identified Twin."""

    if candidate.kind != "push":
        raise ValueError("in-world validation currently supports push actions only")
    snapshot_id = belief_snapshot_id or physical_belief_snapshot_id(belief)
    protocol_id = experiment_protocol_id or f"physical-experiment:{protocol_sha256(protocol)}"
    world.settle()
    candidate_protocol = configure_push_protocol(
        protocol.final_push,
        dt_s=config.dt_s,
        speed_m_s=candidate.speed_m_s,
        push_height_m=candidate.push_height_m,
        lateral_contact_offset_m=candidate.lateral_contact_offset_m,
        contact_surface_id=candidate.contact_surface_id,
        safety_stop_fraction=1.0,
    )
    execution = world.execute_push(
        candidate.target_m,
        candidate.max_force_n,
        protocol=candidate_protocol,
    )
    samples = execution.samples
    observation = world.observe_object_pose()
    final = np.asarray(observation.pose.position_m)
    final_orientation = observation.pose.orientation_xyzw
    task = get_task_preset(config.task_id)
    goal_result = evaluate_planar_goal(
        task.goal,
        PlanarPose(
            x_m=float(final[0]),
            y_m=float(final[1]),
            yaw_rad=yaw_from_quaternion_xyzw(final_orientation),
        ),
    )
    error = goal_result.position_error_m
    peak = max((np.linalg.norm(s.measured_force_n) for s in samples), default=0.0)
    quat = observation.pose.orientation_xyzw
    upright_cos = 1.0 - 2.0 * (quat[0] ** 2 + quat[1] ** 2)
    tilt = math.acos(float(np.clip(upright_cos, -1.0, 1.0)))
    feasible = (
        not execution.safety_tripped and peak <= candidate.max_force_n * 1.01 and tilt < math.radians(15)
    )
    yaw_penalty = (goal_result.yaw_error_rad or 0.0) / math.pi
    success_probability = float(np.clip(1.0 - error / 0.10 - tilt / math.pi - yaw_penalty, 0.0, 1.0))
    return PredictedOutcome(
        action_id=candidate.action_id,
        final_position_m=tuple(float(v) for v in final),
        final_orientation_xyzw=final_orientation,
        target_error_m=error,
        target_yaw_error_rad=goal_result.yaw_error_rad,
        success_probability=success_probability,
        tipping_margin=math.radians(15) - tilt,
        peak_force_n=float(peak),
        duration_s=len(samples) * config.dt_s,
        feasible=feasible,
        rationale=(
            "independent Newton belief rollout with bounded force controller; "
            f"controller outcome={execution.outcome.value}"
        ),
        scenario_id=candidate.action_id,
        world_instance_id=world.world_instance_id,
        world_config_id=world.world_config_id,
        experiment_protocol_id=protocol_id,
        belief_revision=belief.revision,
        belief_snapshot_id=snapshot_id,
    )
