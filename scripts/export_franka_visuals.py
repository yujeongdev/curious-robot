"""Export the official Newton-pinned Franka FR3 visual meshes to web GLB files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import newton
import trimesh

SOURCE_REPOSITORY = "https://github.com/frankarobotics/franka_description"
SOURCE_COMMIT = "d9841cf"
ARM_VISUALS = {f"fr3__fr3_link{index}": f"link{index}.dae" for index in range(8)}
HAND_VISUALS = {
    "fr3__fr3_hand": "hand.dae",
    "fr3__fr3_leftfinger": "finger.dae",
    "fr3__fr3_rightfinger": "finger.dae",
}


def _add_link(combined: trimesh.Scene, body_id: str, source: Path) -> dict[str, object]:
    scene = trimesh.load(source, force="scene")
    if not isinstance(scene, trimesh.Scene) or not scene.geometry:
        raise RuntimeError(f"official visual mesh is empty: {source}")
    combined.graph.update(frame_to=body_id, frame_from=combined.graph.base_frame)
    for index, node in enumerate(scene.graph.nodes_geometry):
        transform, geometry_name = scene.graph[node]
        combined.add_geometry(
            scene.geometry[geometry_name].copy(),
            node_name=f"{body_id}__part_{index:02d}",
            geom_name=f"{body_id}__geometry_{index:02d}",
            parent_node_name=body_id,
            transform=transform,
        )
    return {
        "node": body_id,
        "source": source.name,
        "geometry_count": len(scene.geometry),
        "bounds_m": scene.bounds.tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/web/public/models/franka"),
        help="web asset directory",
    )
    args = parser.parse_args()
    asset_root = Path(newton.utils.download_asset("franka_emika_panda"))
    source_readme = (asset_root / "README.md").read_text()
    if SOURCE_COMMIT not in source_readme:
        raise RuntimeError(
            "Newton Franka asset revision changed; audit the new official source before export"
        )
    arm_root = asset_root / "meshes/robot_arms/fr3/visual"
    hand_root = asset_root / "meshes/robot_ee/franka_hand_white/visual"
    args.output.mkdir(parents=True, exist_ok=True)

    for stale in args.output.glob("*.glb"):
        stale.unlink()
    combined = trimesh.Scene(base_frame="world")
    links: dict[str, dict[str, object]] = {}
    for body_id, filename in ARM_VISUALS.items():
        links[body_id] = _add_link(combined, body_id, arm_root / filename)
    for body_id, filename in HAND_VISUALS.items():
        links[body_id] = _add_link(combined, body_id, hand_root / filename)

    glb_path = args.output / "fr3.glb"
    glb_path.write_bytes(trimesh.exchange.gltf.export_glb(combined))
    roundtrip = trimesh.load(glb_path, force="scene")
    missing = set(links).difference(roundtrip.graph.nodes)
    if missing:
        raise RuntimeError(f"GLB roundtrip lost Franka link roots: {sorted(missing)}")
    glb_sha256 = hashlib.sha256(glb_path.read_bytes()).hexdigest()
    for link in links.values():
        link["file"] = "/models/franka/fr3.glb"

    shutil.copyfile(asset_root / "LICENSE", args.output / "LICENSE")
    manifest = {
        "manifest_version": "2.0.0",
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "source_license": "Apache-2.0",
        "coordinate_system": "right-handed, meters, link-local mesh coordinates; Newton world is Z-up",
        "consumer": "React Three Fiber presentation mirror",
        "presentation_only": True,
        "glb_sha256": glb_sha256,
        "links": links,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"exported {len(links)} official Franka link visuals to {args.output}")


if __name__ == "__main__":
    main()
