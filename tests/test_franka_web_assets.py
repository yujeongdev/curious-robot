from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_official_franka_glb_manifest_is_complete_and_content_addressed() -> None:
    root = Path("src/web/public/models/franka")
    manifest = json.loads((root / "manifest.json").read_text())
    glb = root / "fr3.glb"
    expected = {
        *(f"fr3__fr3_link{index}" for index in range(8)),
        "fr3__fr3_hand",
        "fr3__fr3_leftfinger",
        "fr3__fr3_rightfinger",
    }

    assert manifest["source_repository"] == "https://github.com/frankarobotics/franka_description"
    assert manifest["source_commit"] == "d9841cf"
    assert manifest["source_license"] == "Apache-2.0"
    assert set(manifest["links"]) == expected
    assert hashlib.sha256(glb.read_bytes()).hexdigest() == manifest["glb_sha256"]
    assert glb.read_bytes()[:4] == b"glTF"
    for body_id, link in manifest["links"].items():
        assert link["node"] == body_id
        assert link["file"] == "/models/franka/fr3.glb"
        assert link["geometry_count"] >= 1
