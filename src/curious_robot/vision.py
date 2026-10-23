"""Artifact-backed wrist-camera and vision-estimate contracts."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
SHA256_ID = r"^sha256:[0-9a-f]{64}$"


class _VisionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class CameraIntrinsics(_VisionModel):
    schema_version: Literal["camera-intrinsics-v1"] = "camera-intrinsics-v1"
    camera_id: str = Field(min_length=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    fx_px: float = Field(gt=0.0)
    fy_px: float = Field(gt=0.0)
    cx_px: float = Field(ge=0.0)
    cy_px: float = Field(ge=0.0)
    distortion_model: Literal["none", "brown_conrady"] = "none"
    distortion_coefficients: tuple[float, ...] = ()
    depth_scale_m: float = Field(gt=0.0)
    minimum_depth_m: float = Field(gt=0.0)
    maximum_depth_m: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_camera_model(self) -> CameraIntrinsics:
        if self.cx_px >= self.width_px or self.cy_px >= self.height_px:
            raise ValueError("camera principal point must lie inside the image")
        if self.minimum_depth_m >= self.maximum_depth_m:
            raise ValueError("camera depth range must be strictly ordered")
        if self.distortion_model == "none" and self.distortion_coefficients:
            raise ValueError("undistorted camera cannot declare distortion coefficients")
        if self.distortion_model == "brown_conrady" and len(self.distortion_coefficients) != 5:
            raise ValueError("Brown-Conrady calibration requires five coefficients")
        return self

    @property
    def intrinsics_id(self) -> str:
        return _content_id(self.model_dump_json())


class WristCameraCalibration(_VisionModel):
    schema_version: Literal["wrist-camera-calibration-v1"] = "wrist-camera-calibration-v1"
    robot_serial: str = Field(min_length=1)
    tool_serial: str = Field(min_length=1)
    camera_serial: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    intrinsics_id: str = Field(pattern=SHA256_ID)
    end_effector_frame_id: str = Field(min_length=1)
    optical_frame_id: str = Field(min_length=1)
    ee_to_camera_translation_m: Vec3
    ee_to_camera_orientation_xyzw: Quat
    camera_time_offset_s: float
    maximum_sync_error_s: float = Field(gt=0.0, le=0.1)
    approval_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_extrinsics(self) -> WristCameraCalibration:
        _require_unit_quaternion(self.ee_to_camera_orientation_xyzw)
        return self

    @property
    def calibration_id(self) -> str:
        return _content_id(self.model_dump_json())


class CameraFrameObservation(_VisionModel):
    schema_version: Literal["camera-frame-v1"] = "camera-frame-v1"
    episode_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    sequence: int = Field(gt=0)
    camera_id: str = Field(min_length=1)
    optical_frame_id: str = Field(min_length=1)
    captured_monotonic_s: float = Field(ge=0.0)
    robot_state_monotonic_s: float = Field(ge=0.0)
    captured_wall_time: datetime
    rgb_artifact_id: str = Field(pattern=SHA256_ID)
    depth_artifact_id: str = Field(pattern=SHA256_ID)
    intrinsics_id: str = Field(pattern=SHA256_ID)
    calibration_id: str = Field(pattern=SHA256_ID)
    rgb_encoding: Literal["rgb8", "bgr8", "mono8"]
    depth_encoding: Literal["z16", "float32_m"]
    rgb_compression: Literal["none", "png", "jpeg"]
    depth_compression: Literal["none", "png", "zstd"]
    depth_registered_to_rgb: bool
    exposure_us: float | None = Field(default=None, gt=0.0)
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_artifacts(self) -> CameraFrameObservation:
        if self.captured_wall_time.tzinfo is None:
            raise ValueError("camera wall timestamp must include a timezone")
        if self.rgb_artifact_id == self.depth_artifact_id:
            raise ValueError("RGB and depth must reference distinct artifacts")
        if len(set(self.quality_flags)) != len(self.quality_flags):
            raise ValueError("camera quality flags must be unique")
        return self

    @property
    def observation_id(self) -> str:
        return _content_id(self.model_dump_json(exclude_none=False))


class VisionPoseEstimate(_VisionModel):
    schema_version: Literal["vision-pose-v1"] = "vision-pose-v1"
    estimate_id: str = Field(pattern=SHA256_ID)
    episode_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    frame_id: str = Field(min_length=1)
    position_m: Vec3
    orientation_xyzw: Quat
    covariance_convention: Literal["base_xyz_rotvec"] = Field(
        default="base_xyz_rotvec",
        description=(
            "Row-major covariance of base-frame [x,y,z,rx,ry,rz] perturbations; "
            "translation is metres and rotation-vector components are radians"
        ),
    )
    covariance_6x6: tuple[float, ...] = Field(min_length=36, max_length=36)
    contributing_frame_ids: tuple[str, ...] = Field(min_length=1)
    estimator_id: str = Field(min_length=1)
    estimator_version: str = Field(min_length=1)
    maximum_corner_residual_m: float = Field(ge=0.0)
    rms_corner_residual_m: float = Field(ge=0.0)
    occlusion_fraction: float = Field(ge=0.0, le=1.0)
    valid_depth_fraction: float = Field(ge=0.0, le=1.0)
    captured_monotonic_s: float = Field(ge=0.0)
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_estimate(self) -> VisionPoseEstimate:
        _require_unit_quaternion(self.orientation_xyzw)
        if len(set(self.contributing_frame_ids)) != len(self.contributing_frame_ids):
            raise ValueError("vision estimate frame IDs must be unique")
        if any(not _is_sha256_id(value) for value in self.contributing_frame_ids):
            raise ValueError("vision estimate frame IDs must be SHA-256 observation IDs")
        for row in range(6):
            if self.covariance_6x6[row * 6 + row] < 0.0:
                raise ValueError("vision covariance diagonal must be non-negative")
            for column in range(row + 1, 6):
                if not math.isclose(
                    self.covariance_6x6[row * 6 + column],
                    self.covariance_6x6[column * 6 + row],
                    abs_tol=1.0e-12,
                ):
                    raise ValueError("vision covariance must be symmetric")
        covariance = np.asarray(self.covariance_6x6, dtype=float).reshape((6, 6))
        if float(np.linalg.eigvalsh(covariance).min()) < -1.0e-12:
            raise ValueError("vision covariance must be positive semidefinite")
        if self.rms_corner_residual_m > self.maximum_corner_residual_m:
            raise ValueError("RMS corner residual cannot exceed the maximum residual")
        return self


class WristVisionPerception(Protocol):
    def capture_wrist_rgbd(self, episode_id: str, timeout_s: float) -> CameraFrameObservation: ...

    def estimate_object_pose(
        self,
        frame: CameraFrameObservation,
        object_id: str,
        timeout_s: float,
    ) -> VisionPoseEstimate: ...


def validate_camera_frame_binding(
    frame: CameraFrameObservation,
    intrinsics: CameraIntrinsics,
    calibration: WristCameraCalibration,
) -> None:
    """Validate identities and corrected camera/robot capture-time agreement."""

    if (
        frame.camera_id != intrinsics.camera_id
        or frame.camera_id != calibration.camera_id
        or frame.optical_frame_id != calibration.optical_frame_id
        or frame.intrinsics_id != intrinsics.intrinsics_id
        or frame.intrinsics_id != calibration.intrinsics_id
        or frame.calibration_id != calibration.calibration_id
    ):
        raise ValueError("camera frame identity does not match calibration")
    corrected_camera_time_s = frame.captured_monotonic_s + calibration.camera_time_offset_s
    if (
        abs(corrected_camera_time_s - frame.robot_state_monotonic_s)
        > calibration.maximum_sync_error_s
    ):
        raise ValueError("camera frame and robot state exceed the synchronization bound")


def _content_id(document: str) -> str:
    return f"sha256:{hashlib.sha256(document.encode()).hexdigest()}"


def _is_sha256_id(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def _require_unit_quaternion(value: Quat) -> None:
    if not math.isclose(math.sqrt(sum(component * component for component in value)), 1.0, abs_tol=1e-6):
        raise ValueError("vision orientation must be a unit quaternion")
