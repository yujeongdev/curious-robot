#!/usr/bin/env python3
"""Reject frames where the rendered Franka mesh enters recorded object geometry."""

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


def _link_meshes(model_path: Path) -> dict[str, tuple[tuple[np.ndarray, np.ndarray], ...]]:
    scene = trimesh.load(model_path, force="scene")
    grouped: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for node in scene.graph.nodes_geometry:
        link = node.split("__part_", 1)[0]
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        vertices = np.c_[geometry.vertices, np.ones(len(geometry.vertices))]
        transformed = (transform @ vertices.T).T[:, :3]
        grouped.setdefault(link, []).append(
            (transformed, np.asarray(geometry.faces, dtype=int))
        )
    return {link: tuple(parts) for link, parts in grouped.items()}


def _clip_polygon_to_plane(
    polygon: list[np.ndarray],
    *,
    axis: int,
    boundary: float,
    keep_less: bool,
) -> list[np.ndarray]:
    if not polygon:
        return []

    def signed_distance(point: np.ndarray) -> float:
        return boundary - point[axis] if keep_less else point[axis] - boundary

    clipped: list[np.ndarray] = []
    previous = polygon[-1]
    previous_distance = signed_distance(previous)
    for current in polygon:
        current_distance = signed_distance(current)
        previous_inside = previous_distance >= -1.0e-12
        current_inside = current_distance >= -1.0e-12
        if previous_inside != current_inside:
            denominator = previous_distance - current_distance
            if abs(denominator) > 1.0e-15:
                clipped.append(
                    previous
                    + (current - previous) * (previous_distance / denominator)
                )
        if current_inside:
            clipped.append(current)
        previous = current
        previous_distance = current_distance
    return clipped


def _triangle_box_penetration_m(triangle: np.ndarray, half_extents: np.ndarray) -> float:
    """Return an interior depth for a triangle clipped by an axis-aligned box."""

    polygon = [np.asarray(vertex, dtype=float) for vertex in triangle]
    for axis in range(3):
        polygon = _clip_polygon_to_plane(
            polygon,
            axis=axis,
            boundary=float(half_extents[axis]),
            keep_less=True,
        )
        polygon = _clip_polygon_to_plane(
            polygon,
            axis=axis,
            boundary=-float(half_extents[axis]),
            keep_less=False,
        )
        if len(polygon) < 3:
            return 0.0
    centroid = np.mean(polygon, axis=0)
    return max(0.0, float(np.min(half_extents - np.abs(centroid))))


def _recorded_object_geometry(
    payload: dict[str, object],
    *,
    legacy_cube_edge_m: float,
) -> tuple[str, tuple[tuple[np.ndarray, np.ndarray], ...]]:
    """Return body ID and public box-part transforms from a visual event.

    Legacy recordings predate ``object_geometry`` and are interpreted as the
    documented 50 mm cube. No physical parameter or evaluator data is read.
    """

    geometry = payload.get("object_geometry")
    if geometry is None:
        return (
            "unknown_cube",
            (
                (
                    np.eye(4),
                    np.full(3, legacy_cube_edge_m / 2.0, dtype=float),
                ),
            ),
        )
    if not isinstance(geometry, dict):
        raise ValueError("visual frame object_geometry must be an object")
    body_id = geometry.get("body_id")
    parts = geometry.get("parts")
    if not isinstance(body_id, str) or not body_id:
        raise ValueError("visual frame object_geometry requires body_id")
    if not isinstance(parts, list) or not parts:
        raise ValueError("visual frame object_geometry requires box parts")
    resolved: list[tuple[np.ndarray, np.ndarray]] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("object geometry part must be an object")
        center = part.get("center_object_m")
        half_extents = part.get("half_extents_m")
        orientation = part.get("orientation_object_xyzw", [0.0, 0.0, 0.0, 1.0])
        if not (
            isinstance(center, list | tuple)
            and isinstance(half_extents, list | tuple)
            and isinstance(orientation, list | tuple)
            and len(center) == 3
            and len(half_extents) == 3
            and len(orientation) == 4
        ):
            raise ValueError("object geometry box part has invalid transform or extents")
        half = np.asarray(half_extents, dtype=float)
        if not np.isfinite(half).all() or np.any(half <= 0.0):
            raise ValueError("object geometry half extents must be finite and positive")
        resolved.append((_transform(list(center), list(orientation)), half))
    return body_id, tuple(resolved)


def check_recording(
    events_path: Path,
    model_path: Path,
    *,
    cube_edge_m: float = 0.05,
) -> dict[str, object]:
    links = _link_meshes(model_path)
    maximum_depth_m = 0.0
    maximum_depth_context: dict[str, object] | None = None
    offending_frames: set[tuple[str, str, int]] = set()
    visual_frame_count = 0

    with events_path.open() as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("type") != "simulation.visual_frame":
                continue
            payload = event["payload"]
            bodies = {body["body_id"]: body for body in payload["bodies"]}
            object_body_id, parts = _recorded_object_geometry(
                payload,
                legacy_cube_edge_m=cube_edge_m,
            )
            object_body = bodies.get(object_body_id)
            if object_body is None:
                if payload.get("object_geometry") is not None:
                    raise ValueError(
                        f"visual frame object_geometry body_id {object_body_id!r} is absent"
                    )
                continue
            visual_frame_count += 1
            object_inverse = np.linalg.inv(
                _transform(object_body["position_m"], object_body["orientation_xyzw"])
            )
            for link, meshes in links.items():
                body = bodies.get(link)
                if body is None:
                    continue
                link_transform = _transform(body["position_m"], body["orientation_xyzw"])
                for vertices, faces in meshes:
                    homogeneous = np.c_[vertices, np.ones(len(vertices))]
                    world_vertices = link_transform @ homogeneous.T
                    object_vertices = object_inverse @ world_vertices
                    for part_index, (part_transform, half_extents) in enumerate(parts):
                        local_vertices = (
                            np.linalg.inv(part_transform) @ object_vertices
                        ).T[:, :3]
                        if np.any(np.max(local_vertices, axis=0) < -half_extents) or np.any(
                            np.min(local_vertices, axis=0) > half_extents
                        ):
                            continue
                        local_triangles = local_vertices[faces]
                        triangle_minimum = np.min(local_triangles, axis=1)
                        triangle_maximum = np.max(local_triangles, axis=1)
                        candidates = np.all(
                            (triangle_maximum >= -half_extents)
                            & (triangle_minimum <= half_extents),
                            axis=1,
                        )
                        penetration = max(
                            (
                                _triangle_box_penetration_m(triangle, half_extents)
                                for triangle in local_triangles[candidates]
                            ),
                            default=0.0,
                        )
                        if penetration <= 0.0:
                            continue
                        if penetration > maximum_depth_m:
                            maximum_depth_m = penetration
                            maximum_depth_context = {
                                "scene": str(payload.get("scene", "unknown")),
                                "world_instance_id": str(
                                    payload.get("world_instance_id", "unknown")
                                ),
                                "frame_id": int(payload["frame_id"]),
                                "robot_link": link,
                                "object_part_index": part_index,
                            }
                        offending_frames.add(
                            (
                                str(payload.get("scene", "unknown")),
                                str(payload.get("world_instance_id", "unknown")),
                                int(payload["frame_id"]),
                            )
                        )

    if visual_frame_count == 0:
        raise ValueError("recording contains no visual frames with a recorded object body")
    return {
        "visual_frame_count": visual_frame_count,
        "offending_frame_count": len(offending_frames),
        "maximum_visual_penetration_m": maximum_depth_m,
        "maximum_visual_penetration_context": maximum_depth_context,
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
