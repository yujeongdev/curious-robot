from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_visual_clearance.py"
MODEL = ROOT / "src" / "web" / "public" / "models" / "franka" / "fr3.glb"
_SPEC = importlib.util.spec_from_file_location("check_visual_clearance", CHECKER)
assert _SPEC is not None and _SPEC.loader is not None
check_visual_clearance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_visual_clearance)


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


def _compound_frame(
    object_position: tuple[float, float, float],
    object_orientation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
) -> dict[str, object]:
    return {
        "type": "simulation.visual_frame",
        "payload": {
            "scene": "belief",
            "world_instance_id": "tbar-world",
            "frame_id": 7,
            "object_geometry": {
                "archetype_id": "t-bar",
                "archetype_version": "1.0.0",
                "geometry_hash": "sha256:" + "a" * 64,
                "body_id": "unknown_t_bar",
                "parts": [
                    {
                        "part_id": "stem",
                        "center_object_m": [0.0, 0.0, 0.0],
                        "half_extents_m": [0.04, 0.01, 0.02],
                        "orientation_object_xyzw": [0.0, 0.0, 0.0, 1.0],
                    },
                    {
                        "part_id": "crossbar",
                        "center_object_m": [0.05, 0.0, 0.0],
                        "half_extents_m": [0.01, 0.05, 0.02],
                        "orientation_object_xyzw": [0.0, 0.0, 0.0, 1.0],
                    },
                ],
            },
            "bodies": [
                {
                    "body_id": "fr3__fr3_leftfinger",
                    "position_m": [0.0, 0.0, 0.0],
                    "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
                {
                    "body_id": "unknown_t_bar",
                    "position_m": list(object_position),
                    "orientation_xyzw": list(object_orientation),
                },
            ],
        },
    }


def _check_compound(
    tmp_path: Path,
    monkeypatch,
    object_position: tuple[float, float, float],
    *,
    object_orientation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    link_triangle: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] = ((-0.001, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.001, 0.0)),
):
    events = tmp_path / "compound.ndjson"
    events.write_text(json.dumps(_compound_frame(object_position, object_orientation)) + "\n")
    monkeypatch.setattr(
        check_visual_clearance,
        "_link_meshes",
        lambda _path: {
            "fr3__fr3_leftfinger": (
                (np.asarray(link_triangle), np.asarray([[0, 1, 2]], dtype=int)),
            )
        },
    )
    return check_visual_clearance.check_recording(events, tmp_path / "unused.glb")


def test_media_clearance_rejects_visual_mesh_inside_tbar_part(tmp_path: Path, monkeypatch) -> None:
    report = _check_compound(tmp_path, monkeypatch, (0.0, 0.0, 0.0))
    assert report["offending_frame_count"] == 1
    assert report["maximum_visual_penetration_m"] > 0.009


def test_media_clearance_accepts_visual_mesh_clear_of_all_tbar_parts(tmp_path: Path, monkeypatch) -> None:
    report = _check_compound(tmp_path, monkeypatch, (1.0, 1.0, 1.0))
    assert report == {
        "visual_frame_count": 1,
        "offending_frame_count": 0,
        "maximum_visual_penetration_m": 0.0,
        "maximum_visual_penetration_context": None,
    }


def test_media_clearance_applies_recorded_object_rotation_to_compound_parts(
    tmp_path: Path, monkeypatch
) -> None:
    half_turn_sine = 2.0**-0.5
    report = _check_compound(
        tmp_path,
        monkeypatch,
        (0.0, 0.0, 0.0),
        object_orientation=(0.0, 0.0, half_turn_sine, half_turn_sine),
        link_triangle=((-0.001, 0.05, 0.0), (0.001, 0.05, 0.0), (0.0, 0.051, 0.0)),
    )
    assert report["offending_frame_count"] == 1
    assert report["maximum_visual_penetration_m"] > 0.009


def test_media_clearance_rejects_triangle_crossing_part_with_all_vertices_outside(
    tmp_path: Path, monkeypatch
) -> None:
    crossing_triangle = ((-0.10, -0.10, 0.0), (0.10, -0.10, 0.0), (0.0, 0.10, 0.0))
    assert all(
        not (-0.04 < x < 0.04 and -0.01 < y < 0.01 and -0.02 < z < 0.02)
        for x, y, z in crossing_triangle
    )
    report = _check_compound(
        tmp_path,
        monkeypatch,
        (0.0, 0.0, 0.0),
        link_triangle=crossing_triangle,
    )
    assert report["offending_frame_count"] == 1
    assert report["maximum_visual_penetration_m"] > 0.009


def test_media_clearance_fails_closed_when_manifest_body_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    frame = _compound_frame((0.0, 0.0, 0.0))
    frame["payload"]["bodies"] = frame["payload"]["bodies"][:1]  # type: ignore[index]
    events = tmp_path / "missing-body.ndjson"
    events.write_text(json.dumps(frame) + "\n")
    monkeypatch.setattr(
        check_visual_clearance,
        "_link_meshes",
        lambda _path: {
            "fr3__fr3_leftfinger": (
                (
                    np.asarray(
                        [(-0.001, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.001, 0.0)]
                    ),
                    np.asarray([[0, 1, 2]], dtype=int),
                ),
            )
        },
    )
    with pytest.raises(ValueError, match="body_id 'unknown_t_bar' is absent"):
        check_visual_clearance.check_recording(events, tmp_path / "unused.glb")
