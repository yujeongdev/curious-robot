import { describe, expect, it } from "vitest";
import type { TargetRegion } from "./target-mapping";
import { resolveTargetMarker } from "./target-mapping";

const tBarTarget: TargetRegion = {
  center_m: [0.15, 0, 0.002],
  radius_m: 0.03,
  yaw_rad: Math.PI / 4,
  yaw_tolerance_rad: Math.PI / 20,
  geometry: {
    archetype_id: "t-bar",
    archetype_version: "1.0.0",
    geometry_hash: `sha256:${"a".repeat(64)}`,
    parts: [
      { part_id: "stem", center_object_m: [-0.0125, 0, 0], half_extents_m: [0.0375, 0.0125, 0.02], orientation_object_xyzw: [0, 0, 0, 1] },
      { part_id: "crossbar", center_object_m: [0.0375, 0, 0], half_extents_m: [0.0125, 0.05, 0.02], orientation_object_xyzw: [0, 0, 0, 1] },
    ],
  },
};

describe("target marker binding", () => {
  it("uses every recorded T-bar part without changing its dimensions", () => {
    const marker = resolveTargetMarker(tBarTarget);
    expect(marker.kind).toBe("shape");
    if (marker.kind !== "shape") throw new Error("expected shape marker");
    expect(marker.parts.map((part) => part.part_id)).toEqual(["stem", "crossbar"]);
    expect(marker.parts[1]?.half_extents_m).toEqual([0.0125, 0.05, 0.02]);
  });

  it("preserves the ring fallback for legacy cube recordings", () => {
    expect(resolveTargetMarker({ center_m: [0, 0, 0], radius_m: 0.06, yaw_rad: 0 })).toEqual({
      kind: "ring",
      radiusM: 0.06,
    });
  });
});
