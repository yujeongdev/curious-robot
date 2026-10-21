"""Deterministic Cartesian tool-tip plans for Franka hardware transports."""

from __future__ import annotations

import hashlib
import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from curious_robot.protocol import PushProtocol
from curious_robot.schemas import ActionCandidate
from curious_robot.trajectory import minimum_jerk_blend, minimum_jerk_duration_s

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
MINIMUM_JERK_PEAK_ACCELERATION_FACTOR = 10.0 / math.sqrt(3.0)


class _MotionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class HardwareMotionPhase(StrEnum):
    SAFE_LIFT = "safe_lift"
    PRECONTACT_TRANSIT = "precontact_transit"
    CONTACT_DESCENT = "contact_descent"
    CONTACT_APPROACH = "contact_approach"
    CONTACT_PUSH = "contact_push"
    RETREAT = "retreat"
    RETREAT_LIFT = "retreat_lift"


class ContactExpectation(StrEnum):
    UNEXPECTED = "unexpected"
    ACQUIRE_AT_END = "acquire_at_end"
    MAINTAIN = "maintain"
    RELEASE = "release"


class FrankaPushCalibration(_MotionModel):
    """Versioned public transform and contact geometry for one physical tool."""

    schema_version: str = "franka-push-calibration-v1"
    capability_frame_id: str = Field(min_length=1)
    hardware_frame_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    robot_serial: str = Field(min_length=1)
    tool_serial: str = Field(min_length=1)
    approval_revision: str = Field(min_length=1)
    capability_to_hardware_yaw_rad: float
    capability_to_hardware_translation_m: Vec3
    tcp_to_tip_translation_m: Vec3
    tcp_to_tip_orientation_xyzw: Quat
    required_tip_orientation_xyzw: Quat
    object_half_extent_xy_m: float = Field(gt=0.0)
    tool_tip_radius_m: float = Field(gt=0.0)
    maximum_object_tilt_rad: float = Field(default=0.0, ge=0.0, le=0.0)
    control_period_s: float = Field(gt=0.0, le=0.01)
    retreat_distance_m: float = Field(gt=0.0, le=0.1)
    maximum_waypoint_count: int = Field(gt=0, le=1_000_000)
    tool_workspace_min_m: Vec3
    tool_workspace_max_m: Vec3
    maximum_linear_acceleration_m_s2: float = Field(gt=0.0)
    maximum_angular_speed_rad_s: float = Field(gt=0.0)
    maximum_angular_acceleration_rad_s2: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_calibration(self) -> FrankaPushCalibration:
        if any(
            lower >= upper
            for lower, upper in zip(
                self.tool_workspace_min_m,
                self.tool_workspace_max_m,
                strict=True,
            )
        ):
            raise ValueError("tool workspace bounds must be strictly ordered")
        _require_unit_quaternion(self.tcp_to_tip_orientation_xyzw)
        _require_unit_quaternion(self.required_tip_orientation_xyzw)
        return self

    @property
    def calibration_id(self) -> str:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return f"sha256:{digest}"


class HardwareToolPose(_MotionModel):
    frame_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    calibration_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tip_position_m: Vec3
    tip_orientation_xyzw: Quat
    captured_monotonic_s: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_orientation(self) -> HardwareToolPose:
        _require_unit_quaternion(self.tip_orientation_xyzw)
        return self


class CartesianMotionSegment(_MotionModel):
    phase: HardwareMotionPhase
    start_position_m: Vec3
    end_position_m: Vec3
    tip_orientation_xyzw: Quat
    maximum_speed_m_s: float = Field(gt=0.0)
    duration_s: float = Field(gt=0.0)
    force_guard_enabled: bool
    contact_expectation: ContactExpectation

    @model_validator(mode="after")
    def validate_orientation(self) -> CartesianMotionSegment:
        _require_unit_quaternion(self.tip_orientation_xyzw)
        if not self.force_guard_enabled:
            raise ValueError("independent force guard must remain active during all motion")
        return self


class FrankaPushMotionPlan(_MotionModel):
    capability_frame_id: str
    hardware_frame_id: str
    object_id: str
    tool_id: str
    calibration_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    control_period_s: float = Field(gt=0.0)
    segments: tuple[CartesianMotionSegment, ...]
    maximum_force_n: float = Field(gt=0.0)
    maximum_linear_acceleration_m_s2: float = Field(gt=0.0)
    maximum_angular_speed_rad_s: float = Field(gt=0.0)
    maximum_angular_acceleration_rad_s2: float = Field(gt=0.0)
    command_timeout_s: float = Field(gt=0.0)
    planned_duration_s: float = Field(gt=0.0)
    waypoint_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_plan(self) -> FrankaPushMotionPlan:
        if not self.segments:
            raise ValueError("hardware motion plan requires segments")
        for previous, following in zip(self.segments, self.segments[1:], strict=False):
            if previous.end_position_m != following.start_position_m:
                raise ValueError("hardware motion segments must be continuous")
            if previous.tip_orientation_xyzw != following.tip_orientation_xyzw:
                raise ValueError("hardware push orientation must remain fixed")
        expected_duration_s = sum(segment.duration_s for segment in self.segments)
        if not math.isclose(self.planned_duration_s, expected_duration_s):
            raise ValueError("hardware motion duration does not match its segments")
        expected_waypoints = sum(
            round(segment.duration_s / self.control_period_s)
            for segment in self.segments
        )
        if self.waypoint_count != expected_waypoints:
            raise ValueError("hardware waypoint count does not match its segments")
        if not math.isclose(
            self.planned_duration_s,
            self.waypoint_count * self.control_period_s,
        ):
            raise ValueError("hardware motion duration is not control-period quantized")
        if self.planned_duration_s > self.command_timeout_s:
            raise ValueError("hardware motion plan exceeds command timeout")
        return self

    @property
    def plan_id(self) -> str:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return f"sha256:{digest}"


def compile_franka_push_plan(
    *,
    action: ActionCandidate,
    protocol: PushProtocol,
    capability_frame_id: str,
    capability_object_id: str,
    current_object_position_m: Vec3,
    current_object_orientation_xyzw: Quat,
    current_tool_pose: HardwareToolPose,
    calibration: FrankaPushCalibration,
    maximum_force_n: float,
    maximum_speed_m_s: float,
    command_timeout_s: float,
) -> FrankaPushMotionPlan:
    """Compile a capability into a continuous bounded tool-tip path."""

    _validate_identities(
        capability_frame_id=capability_frame_id,
        capability_object_id=capability_object_id,
        current_tool_pose=current_tool_pose,
        calibration=calibration,
    )
    if action.kind != "push":
        raise ValueError("Franka push planner accepts only push actions")
    if maximum_force_n > min(action.max_force_n, protocol.maximum_horizontal_force_n):
        raise ValueError("transport force bound widens the attested push")
    if maximum_speed_m_s > min(action.speed_m_s, protocol.contact_speed_m_s):
        raise ValueError("transport speed bound widens the attested push")
    if action.push_height_m != protocol.push_height_offset_m:
        raise ValueError("action push height does not match the attested protocol")

    target_m = transform_capability_position(action.target_m, calibration)
    delta_x = target_m[0] - current_object_position_m[0]
    delta_y = target_m[1] - current_object_position_m[1]
    object_travel_m = math.hypot(delta_x, delta_y)
    if not math.isfinite(object_travel_m) or object_travel_m <= 0.0:
        raise ValueError("hardware push target must differ from the object position")
    direction_x = delta_x / object_travel_m
    direction_y = delta_y / object_travel_m
    lateral_x, lateral_y = -direction_y, direction_x
    object_yaw = _object_yaw_if_upright(
        current_object_orientation_xyzw,
        calibration.maximum_object_tilt_rad,
    )
    local_direction_x = math.cos(object_yaw) * direction_x + math.sin(object_yaw) * direction_y
    local_direction_y = -math.sin(object_yaw) * direction_x + math.cos(object_yaw) * direction_y
    contact_support_m = calibration.tool_tip_radius_m + calibration.object_half_extent_xy_m * (
        abs(local_direction_x) + abs(local_direction_y)
    )
    start_offset_m = contact_support_m + protocol.precontact_offset_m
    contact_x = (
        current_object_position_m[0]
        - direction_x * start_offset_m
        + lateral_x * protocol.lateral_ik_compensation_m
    )
    contact_y = (
        current_object_position_m[1]
        - direction_y * start_offset_m
        + lateral_y * protocol.lateral_ik_compensation_m
    )
    push_z = current_object_position_m[2] + protocol.push_height_offset_m
    hardware_safe_z = (
        protocol.safe_height_m + calibration.capability_to_hardware_translation_m[2]
    )
    safe_z = max(hardware_safe_z, current_tool_pose.tip_position_m[2], push_z)
    required_push_travel_m = protocol.precontact_offset_m + object_travel_m
    if required_push_travel_m > protocol.maximum_travel_m:
        raise ValueError("hardware push goal exceeds protocol maximum travel")
    lateral_offset = (
        lateral_x * protocol.lateral_ik_compensation_m,
        lateral_y * protocol.lateral_ik_compensation_m,
    )
    push_end = (
        target_m[0] - direction_x * contact_support_m + lateral_offset[0],
        target_m[1] - direction_y * contact_support_m + lateral_offset[1],
        push_z,
    )
    retreat_distance_m = min(calibration.retreat_distance_m, protocol.precontact_offset_m)
    retreat_end = (
        push_end[0] - direction_x * retreat_distance_m,
        push_end[1] - direction_y * retreat_distance_m,
        push_z,
    )
    approach_speed = min(protocol.approach_speed_m_s, maximum_speed_m_s)
    contact_speed = min(protocol.contact_speed_m_s, maximum_speed_m_s)
    current_safe = (
        current_tool_pose.tip_position_m[0],
        current_tool_pose.tip_position_m[1],
        safe_z,
    )
    contact_safe = (contact_x, contact_y, safe_z)
    precontact_start = (contact_x, contact_y, push_z)
    geometric_contact = (
        current_object_position_m[0] - direction_x * contact_support_m + lateral_offset[0],
        current_object_position_m[1] - direction_y * contact_support_m + lateral_offset[1],
        push_z,
    )
    retreat_safe = (retreat_end[0], retreat_end[1], safe_z)
    points = (
        (
            HardwareMotionPhase.SAFE_LIFT,
            current_tool_pose.tip_position_m,
            current_safe,
            approach_speed,
            ContactExpectation.UNEXPECTED,
        ),
        (
            HardwareMotionPhase.PRECONTACT_TRANSIT,
            current_safe,
            contact_safe,
            approach_speed,
            ContactExpectation.UNEXPECTED,
        ),
        (
            HardwareMotionPhase.CONTACT_DESCENT,
            contact_safe,
            precontact_start,
            contact_speed,
            ContactExpectation.UNEXPECTED,
        ),
        (
            HardwareMotionPhase.CONTACT_APPROACH,
            precontact_start,
            geometric_contact,
            contact_speed,
            ContactExpectation.ACQUIRE_AT_END,
        ),
        (
            HardwareMotionPhase.CONTACT_PUSH,
            geometric_contact,
            push_end,
            contact_speed,
            ContactExpectation.MAINTAIN,
        ),
        (
            HardwareMotionPhase.RETREAT,
            push_end,
            retreat_end,
            contact_speed,
            ContactExpectation.RELEASE,
        ),
        (
            HardwareMotionPhase.RETREAT_LIFT,
            retreat_end,
            retreat_safe,
            approach_speed,
            ContactExpectation.UNEXPECTED,
        ),
    )
    segments = tuple(
        _segment(
            phase,
            start,
            end,
            calibration.required_tip_orientation_xyzw,
            speed,
            calibration.maximum_linear_acceleration_m_s2,
            contact_expectation,
            calibration.control_period_s,
        )
        for phase, start, end, speed, contact_expectation in points
        if math.dist(start, end) > 0.0
    )
    waypoint_count = sum(
        round(segment.duration_s / calibration.control_period_s)
        for segment in segments
    )
    if waypoint_count > calibration.maximum_waypoint_count:
        raise ValueError("hardware motion plan exceeds waypoint capacity")
    for segment in segments:
        _require_inside_workspace(segment.start_position_m, calibration)
        _require_inside_workspace(segment.end_position_m, calibration)
    duration_s = waypoint_count * calibration.control_period_s
    return FrankaPushMotionPlan(
        capability_frame_id=capability_frame_id,
        hardware_frame_id=calibration.hardware_frame_id,
        object_id=calibration.object_id,
        tool_id=calibration.tool_id,
        calibration_id=calibration.calibration_id,
        control_period_s=calibration.control_period_s,
        segments=segments,
        maximum_force_n=protocol.safety_stop_fraction * maximum_force_n,
        maximum_linear_acceleration_m_s2=calibration.maximum_linear_acceleration_m_s2,
        maximum_angular_speed_rad_s=calibration.maximum_angular_speed_rad_s,
        maximum_angular_acceleration_rad_s2=calibration.maximum_angular_acceleration_rad_s2,
        command_timeout_s=command_timeout_s,
        planned_duration_s=duration_s,
        waypoint_count=waypoint_count,
    )


def transform_capability_position(position_m: Vec3, calibration: FrankaPushCalibration) -> Vec3:
    """Transform a gravity-aligned capability point into the Franka base frame."""

    cosine = math.cos(calibration.capability_to_hardware_yaw_rad)
    sine = math.sin(calibration.capability_to_hardware_yaw_rad)
    translation = calibration.capability_to_hardware_translation_m
    return (
        cosine * position_m[0] - sine * position_m[1] + translation[0],
        sine * position_m[0] + cosine * position_m[1] + translation[1],
        position_m[2] + translation[2],
    )


def sample_segment(segment: CartesianMotionSegment, control_period_s: float) -> tuple[Vec3, ...]:
    """Sample a minimum-jerk segment including its final waypoint."""

    if control_period_s <= 0.0 or not math.isfinite(control_period_s):
        raise ValueError("control period must be finite and positive")
    count = round(segment.duration_s / control_period_s)
    if count <= 0 or not math.isclose(segment.duration_s, count * control_period_s):
        raise ValueError("segment duration is not control-period quantized")
    samples = tuple(
        tuple(
            start + minimum_jerk_blend(index / count) * (end - start)
            for start, end in zip(
                segment.start_position_m,
                segment.end_position_m,
                strict=True,
            )
        )
        for index in range(1, count + 1)
    )
    return (*samples[:-1], segment.end_position_m)


def _segment(
    phase: HardwareMotionPhase,
    start: Vec3,
    end: Vec3,
    orientation: Quat,
    speed_m_s: float,
    maximum_acceleration_m_s2: float,
    contact_expectation: ContactExpectation,
    control_period_s: float,
) -> CartesianMotionSegment:
    distance_m = math.dist(start, end)
    continuous_duration_s = max(
        minimum_jerk_duration_s(distance_m, speed_m_s),
        math.sqrt(
            MINIMUM_JERK_PEAK_ACCELERATION_FACTOR
            * distance_m
            / maximum_acceleration_m_s2
        ),
    )
    waypoint_count = max(1, math.ceil(continuous_duration_s / control_period_s))
    return CartesianMotionSegment(
        phase=phase,
        start_position_m=start,
        end_position_m=end,
        tip_orientation_xyzw=orientation,
        maximum_speed_m_s=speed_m_s,
        duration_s=waypoint_count * control_period_s,
        force_guard_enabled=True,
        contact_expectation=contact_expectation,
    )


def _validate_identities(
    *,
    capability_frame_id: str,
    capability_object_id: str,
    current_tool_pose: HardwareToolPose,
    calibration: FrankaPushCalibration,
) -> None:
    if capability_frame_id != calibration.capability_frame_id:
        raise ValueError("capability frame does not match calibration")
    if capability_object_id != calibration.object_id:
        raise ValueError("capability object does not match calibration")
    if current_tool_pose.frame_id != calibration.hardware_frame_id:
        raise ValueError("tool pose frame does not match calibration")
    if current_tool_pose.tool_id != calibration.tool_id:
        raise ValueError("tool pose identity does not match calibration")
    if current_tool_pose.calibration_id != calibration.calibration_id:
        raise ValueError("tool pose calibration identity does not match")
    orientation_dot = abs(
        sum(
            observed * required
            for observed, required in zip(
                current_tool_pose.tip_orientation_xyzw,
                calibration.required_tip_orientation_xyzw,
                strict=True,
            )
        )
    )
    if not math.isclose(orientation_dot, 1.0, abs_tol=1e-6):
        raise ValueError("tool pose is not at the calibrated push orientation")


def _require_inside_workspace(position_m: Vec3, calibration: FrankaPushCalibration) -> None:
    if any(not math.isfinite(coordinate) for coordinate in position_m):
        raise ValueError("hardware tool-tip waypoint must be finite")
    if any(
        coordinate < lower or coordinate > upper
        for coordinate, lower, upper in zip(
            position_m,
            calibration.tool_workspace_min_m,
            calibration.tool_workspace_max_m,
            strict=True,
        )
    ):
        raise ValueError("hardware tool-tip waypoint is outside the calibrated workspace")


def _require_unit_quaternion(value: Quat) -> None:
    norm = math.sqrt(sum(component**2 for component in value))
    if not math.isclose(norm, 1.0, abs_tol=1e-6):
        raise ValueError("hardware tool orientation must be a unit quaternion")


def _object_yaw_if_upright(value: Quat, maximum_tilt_rad: float) -> float:
    _require_unit_quaternion(value)
    x, y, z, w = value
    vertical_alignment = min(1.0, max(-1.0, 1.0 - 2.0 * (x * x + y * y)))
    if math.acos(vertical_alignment) > maximum_tilt_rad:
        raise ValueError("hardware object tilt exceeds the calibrated contact model")
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
