from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from curious_robot.vision import (
    CameraFrameObservation,
    CameraIntrinsics,
    VisionPoseEstimate,
    WristCameraCalibration,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        camera_id="wrist-d405",
        width_px=1280,
        height_px=720,
        fx_px=700.0,
        fy_px=700.0,
        cx_px=640.0,
        cy_px=360.0,
        depth_scale_m=0.001,
        minimum_depth_m=0.07,
        maximum_depth_m=0.5,
    )


def test_wrist_calibration_and_intrinsics_are_content_addressed() -> None:
    intrinsics = _intrinsics()
    calibration = WristCameraCalibration(
        robot_serial="FR3-001",
        tool_serial="PUSHER-001",
        camera_serial="D405-001",
        camera_id=intrinsics.camera_id,
        intrinsics_id=intrinsics.intrinsics_id,
        end_effector_frame_id="fr3_hand",
        optical_frame_id="wrist_camera_optical",
        ee_to_camera_translation_m=(0.04, 0.0, 0.02),
        ee_to_camera_orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        camera_time_offset_s=0.001,
        maximum_sync_error_s=0.005,
        approval_revision="hand-eye-board-v1",
    )

    assert intrinsics.intrinsics_id.startswith("sha256:")
    assert calibration.calibration_id.startswith("sha256:")
    assert calibration.calibration_id == WristCameraCalibration.model_validate_json(
        calibration.model_dump_json()
    ).calibration_id

    corrected_clock_bias = WristCameraCalibration.model_validate(
        {**calibration.model_dump(), "camera_time_offset_s": 0.02}
    )
    assert corrected_clock_bias.camera_time_offset_s == pytest.approx(0.02)


def test_camera_frame_contains_artifact_provenance_but_no_hidden_pose() -> None:
    fields = set(CameraFrameObservation.model_fields)
    frame = CameraFrameObservation(
        episode_id="episode-1",
        stream_id="wrist-rgbd",
        sequence=1,
        camera_id="wrist-d405",
        optical_frame_id="wrist_camera_optical",
        captured_monotonic_s=10.0,
        captured_wall_time=datetime.now(UTC),
        rgb_artifact_id=HASH_A,
        depth_artifact_id=HASH_B,
        intrinsics_id=HASH_A,
        calibration_id=HASH_B,
    )

    assert frame.sequence == 1
    assert not {"mass_kg", "friction", "ground_truth_pose", "position_m"} & fields
    with pytest.raises(ValidationError, match="Extra inputs"):
        CameraFrameObservation.model_validate(
            {**frame.model_dump(), "ground_truth_pose": (0.0, 0.0, 0.0)}
        )
    with pytest.raises(ValidationError, match="timezone"):
        CameraFrameObservation.model_validate(
            {**frame.model_dump(), "captured_wall_time": datetime.now()}
        )


def test_vision_pose_rejects_non_symmetric_covariance_and_bad_residual_order() -> None:
    covariance = [0.0] * 36
    covariance[0] = covariance[7] = covariance[14] = 1.0e-5
    covariance[21] = covariance[28] = covariance[35] = 1.0e-4
    values = {
        "estimate_id": HASH_A,
        "episode_id": "episode-1",
        "object_id": "t-bar",
        "frame_id": "franka_base",
        "position_m": (0.1, 0.0, 0.02),
        "orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
        "covariance_6x6": tuple(covariance),
        "contributing_frame_ids": (HASH_B,),
        "estimator_id": "tbar-depth-registration",
        "estimator_version": "1.0.0",
        "maximum_corner_residual_m": 0.01,
        "rms_corner_residual_m": 0.006,
        "occlusion_fraction": 0.1,
        "valid_depth_fraction": 0.9,
        "captured_monotonic_s": 10.0,
    }
    assert VisionPoseEstimate(**values).object_id == "t-bar"

    asymmetric = covariance.copy()
    asymmetric[1] = 1.0e-3
    with pytest.raises(ValidationError, match="symmetric"):
        VisionPoseEstimate(**{**values, "covariance_6x6": tuple(asymmetric)})
    with pytest.raises(ValidationError, match="RMS"):
        VisionPoseEstimate(**{**values, "rms_corner_residual_m": 0.02})
