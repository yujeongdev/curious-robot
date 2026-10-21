from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_visual_clearance.py"
MODEL = ROOT / "src" / "web" / "public" / "models" / "franka" / "fr3.glb"


def _frame(cube_position: tuple[float, float, float]) -> dict[str, object]:
    return {
        "type": "simulation.visual_frame",
        "payload": {
            "scene": "truth",
            "world_instance_id": "fixture-world",
            "frame_id": 1,
            "bodies": [
                {
                    "body_id": "fr3__fr3_leftfinger",
                    "position_m": [0.0, 0.0, 0.0],
                    "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
                {
                    "body_id": "unknown_cube",
                    "position_m": list(cube_position),
                    "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
            ],
        },
    }


def _run(tmp_path: Path, cube_position: tuple[float, float, float]) -> subprocess.CompletedProcess[str]:
    events = tmp_path / "events.ndjson"
    events.write_text(json.dumps(_frame(cube_position)) + "\n")
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(CHECKER),
            "--events",
            str(events),
            "--model",
            str(MODEL),
            "--maximum-penetration-mm",
            "1.0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_media_clearance_accepts_separated_visual_mesh(tmp_path: Path) -> None:
    result = _run(tmp_path, (1.0, 1.0, 1.0))
    assert result.returncode == 0, result.stderr


def test_media_clearance_rejects_franka_visual_mesh_inside_cube(tmp_path: Path) -> None:
    result = _run(tmp_path, (0.0, 0.013, 0.027))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["maximum_visual_penetration_m"] > 0.001
