"""Visual playback handoff and deterministic physics re-execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .environment import PhysicalParameters, TruthEnvironment, WorldConfig


def verify_reexecution(manifest_path: str | Path) -> dict[str, object]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    truth = manifest["evaluation_truth"]
    world_config = manifest["world_config"]
    config = WorldConfig(
        device=world_config["device"],
        dt_s=world_config["dt_s"],
        initial_position_m=tuple(manifest["initial_cube_position_m"]),
        target_position_m=tuple(world_config["target_position_m"]),
        observation_noise_std=0.0,
        force_noise_std=0.0,
        include_robot=world_config["include_robot"],
        seed=manifest["seed"],
    )
    world = TruthEnvironment(
        PhysicalParameters(
            mass_kg=truth["mass_kg"],
            static_friction=truth["static_friction"],
            dynamic_friction=truth["dynamic_friction"],
            com_m=tuple(truth["com_m"]),
        ),
        config,
    )
    actual: list[tuple[float, float, float]] = []
    try:
        world.set_cube_state(
            tuple(manifest["initial_cube_body_q"]),
            tuple(manifest["initial_cube_body_qd"]),
        )
        action = manifest["selected_action"]
        if action["kind"] == "push":
            world.execute_push(tuple(action["target_m"]), action["max_force_n"])
        else:
            world.execute_pick_place(tuple(action["target_m"]), action["max_force_n"])
        actual = list(world.state_trace)
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
        report = {
            "passed": bool(np.max(error, initial=0.0) <= tolerance),
            "max_position_error_m": float(np.max(error, initial=0.0)),
            "rmse_position_m": float(np.sqrt(np.mean(error**2))) if len(error) else 0.0,
            "position_tolerance_m": tolerance,
            "samples": len(error),
            "determinism_scope": "same GPU, driver, packages, solver settings, and control trace",
        }
    report_path = path.with_name("reexecution_report.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


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
