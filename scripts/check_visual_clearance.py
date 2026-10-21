#!/usr/bin/env python3
"""Reject recorded Newton frames where the rendered Franka mesh enters the cube."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh


def _transform(position: list[float], quaternion_xyzw: list[float]) -> np.ndarray:
    x, y, z, w = quaternion_xyzw
    rotation = np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    result = np.eye(4)
    result[:3, :3] = rotation
    result[:3, 3] = position
    return result


def _link_vertices(model_path: Path) -> dict[str, np.ndarray]:
    scene = trimesh.load(model_path, force="scene")
    grouped: dict[str, list[np.ndarray]] = {}
    for node in scene.graph.nodes_geometry:
        link = node.split("__part_", 1)[0]
        transform, geometry_name = scene.graph[node]
        vertices = np.c_[
            scene.geometry[geometry_name].vertices,
            np.ones(len(scene.geometry[geometry_name].vertices)),
        ]
        grouped.setdefault(link, []).append((transform @ vertices.T).T[:, :3])
    return {link: np.vstack(parts) for link, parts in grouped.items()}


def check_recording(
    events_path: Path,
    model_path: Path,
    *,
    cube_edge_m: float = 0.05,
) -> dict[str, object]:
    links = _link_vertices(model_path)
    half_extent = cube_edge_m / 2.0
    maximum_depth_m = 0.0
    offending_frames: set[tuple[str, str, int]] = set()
    visual_frame_count = 0

    with events_path.open() as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("type") != "simulation.visual_frame":
                continue
            payload = event["payload"]
            bodies = {body["body_id"]: body for body in payload["bodies"]}
            cube = bodies.get("unknown_cube")
            if cube is None:
                continue
            visual_frame_count += 1
            cube_inverse = np.linalg.inv(_transform(cube["position_m"], cube["orientation_xyzw"]))
            for link, vertices in links.items():
                body = bodies.get(link)
                if body is None:
                    continue
                homogeneous = np.c_[vertices, np.ones(len(vertices))]
                world_vertices = (_transform(body["position_m"], body["orientation_xyzw"]) @ homogeneous.T).T
                local_vertices = (cube_inverse @ world_vertices.T).T[:, :3]
                inside = np.all(np.abs(local_vertices) < half_extent, axis=1)
                if not inside.any():
                    continue
                depth = np.min(half_extent - np.abs(local_vertices[inside]), axis=1)
                maximum_depth_m = max(maximum_depth_m, float(depth.max(initial=0.0)))
                offending_frames.add(
                    (
                        str(payload.get("scene", "unknown")),
                        str(payload.get("world_instance_id", "unknown")),
                        int(payload["frame_id"]),
                    )
                )

    if visual_frame_count == 0:
        raise ValueError("recording contains no visual frames with unknown_cube")
    return {
        "visual_frame_count": visual_frame_count,
        "offending_frame_count": len(offending_frames),
        "maximum_visual_penetration_m": maximum_depth_m,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--cube-edge-m", type=float, default=0.05)
    parser.add_argument("--maximum-penetration-mm", type=float, default=1.0)
    args = parser.parse_args()
    report = check_recording(args.events, args.model, cube_edge_m=args.cube_edge_m)
    print(json.dumps(report, indent=2))
    if float(report["maximum_visual_penetration_m"]) > args.maximum_penetration_mm / 1000.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
