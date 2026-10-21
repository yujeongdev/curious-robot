from __future__ import annotations

import math

import pytest

from curious_robot.hardware_motion import (
    FrankaPushCalibration,
    HardwareMotionPhase,
    HardwareToolPose,
    compile_franka_push_plan,
    sample_segment,
)
from curious_robot.protocol import load_default_protocol
from curious_robot.schemas import ActionCandidate


def _calibration(**updates) -> FrankaPushCalibration:  # noqa: ANN003
    values = {
        "capability_frame_id": "simulation_world",
        "hardware_frame_id": "franka_base",
        "object_id": "cube",
        "tool_id": "fr3-pusher-tip",
        "robot_serial": "FR3-TEST-001",
        "tool_serial": "PUSHER-TEST-001",
        "approval_revision": "operator-fixture-v1",
        "capability_to_hardware_yaw_rad": 0.0,
        "capability_to_hardware_translation_m": (0.0, 0.0, 0.0),
        "tcp_to_tip_translation_m": (0.0, 0.0, 0.1034),
        "tcp_to_tip_orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
        "required_tip_orientation_xyzw": (0.0, 1.0, 0.0, 0.0),
        "object_half_extent_xy_m": 0.025,
        "tool_tip_radius_m": 0.012,
        "maximum_object_tilt_rad": 0.0,
        "control_period_s": 0.001,
        "retreat_distance_m": 0.04,
        "maximum_waypoint_count": 100_000,
        "tool_workspace_min_m": (-0.5, -0.5, 0.0),
        "tool_workspace_max_m": (0.8, 0.5, 0.8),
        "maximum_linear_acceleration_m_s2": 0.5,
        "maximum_angular_speed_rad_s": 0.5,
        "maximum_angular_acceleration_rad_s2": 1.0,
        "tool_orientation_tolerance_rad": 0.02,
    }
    values.update(updates)
    return FrankaPushCalibration(**values)


def _tool_pose(calibration: FrankaPushCalibration) -> HardwareToolPose:
    return HardwareToolPose(
        frame_id=calibration.hardware_frame_id,
        tool_id=calibration.tool_id,
        calibration_id=calibration.calibration_id,
        tip_position_m=(0.02, 0.0, 0.18),
        tip_orientation_xyzw=calibration.required_tip_orientation_xyzw,
        captured_monotonic_s=100.0,
    )


def _compile(**updates):  # noqa: ANN003, ANN202
    calibration = updates.pop("calibration", _calibration())
    values = {
        "action": ActionCandidate(
            action_id="push-diagonal",
            kind="push",
            target_m=(0.15, 0.05, 0.025),
            speed_m_s=0.04,
            max_force_n=12.0,
            push_height_m=0.015,
        ),
        "protocol": load_default_protocol().final_push,
        "capability_frame_id": "simulation_world",
        "capability_object_id": "cube",
        "current_object_position_m": (0.10, 0.00, 0.025),
        "current_object_orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
        "current_tool_pose": _tool_pose(calibration),
        "calibration": calibration,
        "maximum_force_n": 12.0,
        "maximum_speed_m_s": 0.04,
        "command_timeout_s": 30.0,
    }
    values.update(updates)
    return compile_franka_push_plan(**values)


def test_push_plan_has_continuous_guarded_contact_path() -> None:
    plan = _compile()

    assert tuple(segment.phase for segment in plan.segments) == (
        HardwareMotionPhase.PRECONTACT_TRANSIT,
        HardwareMotionPhase.CONTACT_DESCENT,
        HardwareMotionPhase.CONTACT_APPROACH,
        HardwareMotionPhase.CONTACT_PUSH,
        HardwareMotionPhase.RETREAT,
        HardwareMotionPhase.RETREAT_LIFT,
    )
    assert all(
        left.end_position_m == right.start_position_m
        for left, right in zip(plan.segments, plan.segments[1:], strict=False)
    )
    push = next(segment for segment in plan.segments if segment.phase is HardwareMotionPhase.CONTACT_PUSH)
    assert push.force_guard_enabled
    assert push.end_position_m[0] > push.start_position_m[0]
    assert push.end_position_m[1] > push.start_position_m[1]
    direction = math.sqrt(0.5)
    support_m = 0.012 + 0.025 * (direction + direction)
    assert math.dist(push.end_position_m[:2], (0.15, 0.05)) == pytest.approx(support_m)
    assert all(segment.force_guard_enabled for segment in plan.segments)
    expectations = {segment.phase: segment.contact_expectation.value for segment in plan.segments}
    assert expectations[HardwareMotionPhase.CONTACT_DESCENT] == "unexpected"
    assert expectations[HardwareMotionPhase.CONTACT_APPROACH] == "acquire_at_end"
    assert expectations[HardwareMotionPhase.CONTACT_PUSH] == "maintain"
    assert expectations[HardwareMotionPhase.RETREAT] == "release"
    assert {segment.tip_orientation_xyzw for segment in plan.segments} == {
        plan.segments[0].tip_orientation_xyzw
    }
    assert plan.calibration_id == _calibration().calibration_id
    assert plan.planned_duration_s <= plan.command_timeout_s
    assert plan.maximum_force_n == pytest.approx(6.0)


def test_minimum_jerk_samples_respect_segment_peak_speed() -> None:
    plan = _compile()

    for segment in plan.segments:
        samples = (segment.start_position_m, *sample_segment(segment, plan.control_period_s))
        speeds = [
            math.dist(start, end) / plan.control_period_s
            for start, end in zip(samples, samples[1:], strict=False)
        ]
        assert max(speeds) <= segment.maximum_speed_m_s + 1e-9
        assert samples[-1] == segment.end_position_m


def test_push_plan_rejects_widened_or_unbounded_motion() -> None:
    action = ActionCandidate(
        action_id="push",
        kind="push",
        target_m=(0.15, 0.0, 0.025),
        speed_m_s=0.04,
        max_force_n=12.0,
        push_height_m=0.015,
    )

    with pytest.raises(ValueError, match="speed bound widens"):
        _compile(action=action, maximum_speed_m_s=0.05)
    with pytest.raises(ValueError, match="force bound widens"):
        _compile(action=action, maximum_force_n=13.0)
    with pytest.raises(ValueError, match="exceeds command timeout"):
        _compile(command_timeout_s=1.0)


def test_push_plan_rejects_zero_distance_and_non_push_action() -> None:
    with pytest.raises(ValueError, match="must differ"):
        _compile(
            action=ActionCandidate(
                action_id="push",
                kind="push",
                target_m=(0.10, 0.0, 0.025),
                speed_m_s=0.04,
                max_force_n=12.0,
                push_height_m=0.015,
            )
        )
    with pytest.raises(ValueError, match="only push"):
        _compile(
            action=ActionCandidate(
                action_id="pick",
                kind="pick_place",
                target_m=(0.15, 0.0, 0.025),
                speed_m_s=0.04,
                max_force_n=12.0,
                push_height_m=0.015,
            )
        )


def test_push_plan_rejects_action_protocol_height_mismatch() -> None:
    with pytest.raises(ValueError, match="push height"):
        _compile(
            action=ActionCandidate(
                action_id="push",
                kind="push",
                target_m=(0.15, 0.0, 0.025),
                speed_m_s=0.04,
                max_force_n=12.0,
                push_height_m=0.01,
            )
        )


def test_push_plan_rejects_waypoint_outside_calibrated_workspace() -> None:
    calibration = _calibration(
        tool_workspace_min_m=(0.05, -0.5, 0.0),
    )

    with pytest.raises(ValueError, match="outside the calibrated workspace"):
        _compile(calibration=calibration)


def test_push_plan_rejects_infeasible_travel_instead_of_truncating() -> None:
    with pytest.raises(ValueError, match="maximum travel"):
        _compile(
            action=ActionCandidate(
                action_id="too-far",
                kind="push",
                target_m=(0.45, 0.0, 0.025),
                speed_m_s=0.04,
                max_force_n=12.0,
                push_height_m=0.015,
            )
        )


def test_push_plan_rejects_wrong_tool_frame_orientation_and_nonfinite_pose() -> None:
    calibration = _calibration()
    common = {
        "tool_id": calibration.tool_id,
        "calibration_id": calibration.calibration_id,
        "tip_position_m": (0.02, 0.0, 0.18),
        "tip_orientation_xyzw": calibration.required_tip_orientation_xyzw,
        "captured_monotonic_s": 100.0,
    }
    with pytest.raises(ValueError, match="frame"):
        _compile(current_tool_pose=HardwareToolPose(frame_id="camera", **common))
    with pytest.raises(ValueError, match="capability frame"):
        _compile(capability_frame_id="unbound-frame")
    with pytest.raises(ValueError, match="push orientation"):
        _compile(
            current_tool_pose=HardwareToolPose(
                frame_id=calibration.hardware_frame_id,
                **{**common, "tip_orientation_xyzw": (0.0, 0.0, 0.0, 1.0)},
            )
        )
    with pytest.raises(ValueError, match="finite_number"):
        HardwareToolPose(
            frame_id=calibration.hardware_frame_id,
            **{**common, "tip_position_m": (float("nan"), 0.0, 0.18)},
        )
    angle = 0.2
    with pytest.raises(ValueError, match="object tilt"):
        _compile(
            current_object_orientation_xyzw=(
                math.sin(angle / 2.0),
                0.0,
                0.0,
                math.cos(angle / 2.0),
            )
        )


def test_plan_deadline_uses_quantized_schedule_duration() -> None:
    plan = _compile()
    assert plan.planned_duration_s == pytest.approx(plan.waypoint_count * plan.control_period_s)

    with pytest.raises(ValueError, match="exceeds command timeout"):
        _compile(command_timeout_s=plan.planned_duration_s - 0.5 * plan.control_period_s)


def test_nonidentity_transform_preserves_object_local_support_and_safe_height() -> None:
    yaw = math.pi / 4.0
    calibration = _calibration(
        capability_to_hardware_yaw_rad=yaw,
        capability_to_hardware_translation_m=(1.0, 2.0, 0.2),
        tool_workspace_min_m=(0.0, 1.0, 0.0),
        tool_workspace_max_m=(2.0, 3.0, 1.0),
    )
    object_position = (
        1.0 + math.cos(yaw) * 0.1,
        2.0 + math.sin(yaw) * 0.1,
        0.225,
    )
    object_orientation = (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))
    tool_pose = HardwareToolPose(
        frame_id=calibration.hardware_frame_id,
        tool_id=calibration.tool_id,
        calibration_id=calibration.calibration_id,
        tip_position_m=(object_position[0] - 0.2, object_position[1], 0.38),
        tip_orientation_xyzw=calibration.required_tip_orientation_xyzw,
        captured_monotonic_s=100.0,
    )

    plan = _compile(
        calibration=calibration,
        current_object_position_m=object_position,
        current_object_orientation_xyzw=object_orientation,
        current_tool_pose=tool_pose,
        command_timeout_s=60.0,
    )
    push = next(segment for segment in plan.segments if segment.phase is HardwareMotionPhase.CONTACT_PUSH)
    transformed_target = (
        1.0 + math.cos(yaw) * 0.15 - math.sin(yaw) * 0.05,
        2.0 + math.sin(yaw) * 0.15 + math.cos(yaw) * 0.05,
    )
    expected_support = 0.012 + 0.025 * math.sqrt(2.0)

    assert math.dist(push.end_position_m[:2], transformed_target) == pytest.approx(expected_support)
    maximum_z = max(
        point[2] for segment in plan.segments for point in (segment.start_position_m, segment.end_position_m)
    )
    assert maximum_z == pytest.approx(0.38)


def _rotated_about_x(orientation_xyzw, angle_rad: float):  # noqa: ANN001, ANN202
    """Return ``orientation`` composed with a small rotation about the world x axis."""

    half = angle_rad / 2.0
    ax, aw = math.sin(half), math.cos(half)
    x, y, z, w = orientation_xyzw
    return (
        aw * x + ax * w,
        aw * y - ax * z,
        aw * z + ax * y,
        aw * w - ax * x,
    )


def test_plan_moves_to_protocol_safe_plane_before_transit() -> None:
    calibration = _calibration()
    high_tool = HardwareToolPose(
        frame_id=calibration.hardware_frame_id,
        tool_id=calibration.tool_id,
        calibration_id=calibration.calibration_id,
        tip_position_m=(0.02, 0.0, 0.47),
        tip_orientation_xyzw=calibration.required_tip_orientation_xyzw,
        captured_monotonic_s=100.0,
    )
    safe_height_m = load_default_protocol().final_push.safe_height_m

    plan = _compile(current_tool_pose=high_tool, command_timeout_s=60.0)

    entry = plan.segments[0]
    assert entry.phase is HardwareMotionPhase.SAFE_PLANE_ENTRY
    assert entry.start_position_m == high_tool.tip_position_m
    assert entry.end_position_m == pytest.approx((0.02, 0.0, safe_height_m))
    transit = plan.segments[1]
    assert transit.phase is HardwareMotionPhase.PRECONTACT_TRANSIT
    assert transit.start_position_m[2] == pytest.approx(safe_height_m)
    assert transit.end_position_m[2] == pytest.approx(safe_height_m)
    assert plan.segments[-1].end_position_m[2] == pytest.approx(safe_height_m)


def test_tool_orientation_gate_uses_calibrated_tolerance() -> None:
    calibration = _calibration()

    def pose(angle_rad: float) -> HardwareToolPose:
        return HardwareToolPose(
            frame_id=calibration.hardware_frame_id,
            tool_id=calibration.tool_id,
            calibration_id=calibration.calibration_id,
            tip_position_m=(0.02, 0.0, 0.18),
            tip_orientation_xyzw=_rotated_about_x(calibration.required_tip_orientation_xyzw, angle_rad),
            captured_monotonic_s=100.0,
        )

    plan = _compile(current_tool_pose=pose(0.5 * calibration.tool_orientation_tolerance_rad))
    assert plan.orientation_tolerance_rad == calibration.tool_orientation_tolerance_rad
    with pytest.raises(ValueError, match="push orientation"):
        _compile(current_tool_pose=pose(1.5 * calibration.tool_orientation_tolerance_rad))


@pytest.mark.parametrize("tolerance", [0.0, -0.01, 0.2])
def test_calibration_requires_bounded_orientation_tolerance(tolerance: float) -> None:
    with pytest.raises(ValueError):
        _calibration(tool_orientation_tolerance_rad=tolerance)


def test_orientation_tolerance_is_part_of_calibration_identity() -> None:
    assert (
        _calibration(tool_orientation_tolerance_rad=0.01).calibration_id
        != _calibration(tool_orientation_tolerance_rad=0.02).calibration_id
    )
