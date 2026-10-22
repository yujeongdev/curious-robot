"""Visual playback handoff and deterministic physics re-execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .environment import PhysicalParameters, TruthEnvironment, WorldConfig
from .tasks import wrapped_yaw_error, yaw_from_quaternion_xyzw


def verify_reexecution(manifest_path: str | Path) -> dict[str, object]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    truth = manifest["evaluation_truth"]
    world_config = manifest["world_config"]
    config_fields = WorldConfig.__dataclass_fields__
    config_values = {key: value for key, value in world_config.items() if key in config_fields}
    initial_position, initial_body_q, initial_body_qd = resolve_initial_object_state(manifest)
    config_values.update(
        initial_position_m=initial_position,
        observation_noise_std=0.0,
        force_noise_std=0.0,
        seed=manifest["seed"],
    )
    config = WorldConfig(**config_values)
    world = TruthEnvironment(
        PhysicalParameters(
            mass_kg=truth["mass_kg"],
            static_friction=truth["static_friction"],
            dynamic_friction=truth["dynamic_friction"],
            com_m=tuple(truth["com_m"]),
            restitution=truth.get("restitution", 0.0),
        ),
        config,
    )
    actual: list[tuple[float, float, float]] = []
    actual_orientations: list[tuple[float, float, float, float]] = []
    try:
        world.set_cube_state(
            initial_body_q,
            initial_body_qd,
        )
        world.set_joint_state(
            tuple(manifest["initial_joint_q"]),
            tuple(manifest["initial_joint_qd"]),
            tuple(manifest["initial_joint_target_q"]),
        )
        control_trace = manifest.get("control_sequence_joint_target_q")
        if not isinstance(control_trace, list):
            raise ValueError("reexecution manifest is missing the low-level joint-target trace")
        for joint_target_q in control_trace:
            world.control.joint_target_q.assign(tuple(float(value) for value in joint_target_q))
            world.step()
        actual = list(world.state_trace)
        actual_orientations = list(world.orientation_trace)
    finally:
        world.close()
    expected = np.asarray(manifest["reference_trajectory_position_m"], dtype=float)
    actual_array = np.asarray(actual, dtype=float)
    if expected.shape != actual_array.shape:
        report = {
            "passed": False,
            "reason": "trajectory shape mismatch",
            "expected_shape": list(expected.shape),
            "actual_shape": list(actual_array.shape),
        }
    else:
        error = np.linalg.norm(actual_array - expected, axis=1)
        tolerance = float(manifest["position_tolerance_m"])
        expected_orientations = manifest.get("reference_trajectory_orientation_xyzw")
        max_yaw_error = 0.0
        yaw_tolerance = None
        if expected_orientations is not None:
            if len(expected_orientations) != len(actual_orientations):
                return _write_report(
                    path,
                    {
                        "passed": False,
                        "reason": "orientation trajectory shape mismatch",
                        "expected_samples": len(expected_orientations),
                        "actual_samples": len(actual_orientations),
                    },
                )
            yaw_errors = [
                wrapped_yaw_error(
                    yaw_from_quaternion_xyzw(tuple(actual_quaternion)),
                    yaw_from_quaternion_xyzw(tuple(expected_quaternion)),
                )
                for actual_quaternion, expected_quaternion in zip(
                    actual_orientations,
                    expected_orientations,
                    strict=True,
                )
            ]
            max_yaw_error = max(yaw_errors, default=0.0)
            yaw_tolerance = float(manifest["reexecution_yaw_tolerance_rad"])
        report = {
            "passed": bool(
                np.max(error, initial=0.0) <= tolerance
                and (yaw_tolerance is None or max_yaw_error <= yaw_tolerance)
            ),
            "max_position_error_m": float(np.max(error, initial=0.0)),
            "rmse_position_m": float(np.sqrt(np.mean(error**2))) if len(error) else 0.0,
            "position_tolerance_m": tolerance,
            "max_yaw_error_rad": max_yaw_error if yaw_tolerance is not None else None,
            "yaw_tolerance_rad": yaw_tolerance,
            "samples": len(error),
            "determinism_scope": "same GPU, driver, packages, solver settings, and control trace",
        }
    return _write_report(path, report)


def _write_report(path: Path, report: dict[str, object]) -> dict[str, object]:
    report_path = path.with_name("reexecution_report.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def resolve_initial_object_state(
    manifest: dict[str, object],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Resolve v2 object-neutral state with explicit v1 cube compatibility."""

    def value(new_key: str, legacy_key: str) -> tuple[float, ...]:
        raw = manifest[new_key] if new_key in manifest else manifest[legacy_key]
        if not isinstance(raw, list):
            raise TypeError(f"{new_key} must be an array")
        return tuple(float(item) for item in raw)

    return (
        value("initial_object_position_m", "initial_cube_position_m"),
        value("initial_object_body_q", "initial_cube_body_q"),
        value("initial_object_body_qd", "initial_cube_body_qd"),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?", help="reexecution.json")
    parser.add_argument("--verify", action="store_true", help="re-run the recorded controls")
    parser.add_argument("--visual", type=Path, help="Newton ViewerFile .bin recording")
    args = parser.parse_args(argv)
    if args.visual:
        print("Open the recording with Newton's pause/seek/frame-step UI:")
        print(f"  uv run python -m newton.examples replay_viewer {args.visual}")
    if args.verify:
        if args.manifest is None:
            parser.error("manifest is required with --verify")
        report = verify_reexecution(args.manifest)
        print(json.dumps(report, indent=2))
        if not report["passed"]:
            raise SystemExit(1)
    if not args.verify and not args.visual:
        parser.print_help(sys.stderr)


if __name__ == "__main__":
    main()
