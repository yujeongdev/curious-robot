import { describe, expect, it } from "vitest";
import type { BodyTransform, VisualFramePayload } from "../../generated/agent-events";
import { findCubeBody, findObjectBody, isFrankaBody, resolveObjectVisual } from "./body-mapping";

function body(bodyId: string, label: string): BodyTransform {
  return {
    body_id: bodyId,
    label,
    asset_node_id: bodyId,
    position_m: [0, 0, 0],
    orientation_xyzw: [0, 0, 0, 1],
  };
}

describe("Newton body mapping", () => {
  it("maps the real franka-cube-v1 identifiers", () => {
    const bodies = [body("fr3__fr3_link0", "fr3/fr3_link0"), body("unknown_cube", "unknown_cube")];
    expect(bodies.filter(isFrankaBody)).toHaveLength(1);
    expect(findCubeBody(bodies)?.body_id).toBe("unknown_cube");
  });

  it("retains compatibility with presentation fixtures", () => {
    expect(isFrankaBody(body("panda_hand", "panda/hand"))).toBe(true);
    expect(findCubeBody([body("cube", "cube")])?.body_id).toBe("cube");
  });

  it("resolves the compound T-bar body from its public manifest identity", () => {
    const bodies = [body("unknown_t_bar", "unknown_t_bar")];
    expect(findObjectBody(bodies, "unknown_t_bar")?.body_id).toBe("unknown_t_bar");
    expect(findObjectBody([body("unknown_cube", "unknown_cube")], "unknown_t_bar")).toBeUndefined();
  });

  it("renders T-bar parts only from the public geometry manifest", () => {
    const frame = {
      frame_id: 1,
      source_state_sequence: 1,
      scene: "truth",
      world_instance_id: "reality-world",
      body_manifest_id: "franka-t-bar-v1",
      sim_time_s: 0,
      presentation_time_s: 0,
      bodies: [body("unknown_t_bar", "unknown_t_bar")],
      contacts: [],
      vectors: [],
      object_geometry: {
        archetype_id: "t-bar",
        archetype_version: "1.0.0",
        geometry_hash: `sha256:${"a".repeat(64)}`,
        body_id: "unknown_t_bar",
        parts: [
          { part_id: "stem", center_object_m: [-0.01, 0, 0], half_extents_m: [0.04, 0.01, 0.02], orientation_object_xyzw: [0, 0, 0, 1] },
          { part_id: "crossbar", center_object_m: [0.04, 0, 0], half_extents_m: [0.01, 0.05, 0.02], orientation_object_xyzw: [0, 0, 0, 1] },
        ],
      },
    } satisfies VisualFramePayload;
    expect(resolveObjectVisual(frame)?.parts.map((part) => part.part_id)).toEqual(["stem", "crossbar"]);
    expect(resolveObjectVisual({ ...frame, object_geometry: undefined })).toBeUndefined();
  });

  it("keeps the 50 mm fallback scoped to legacy cube frames", () => {
    const frame = {
      frame_id: 1,
      source_state_sequence: 1,
      scene: "truth",
      world_instance_id: "legacy-world",
      body_manifest_id: "franka-cube-v1",
      sim_time_s: 0,
      presentation_time_s: 0,
      bodies: [body("unknown_cube", "unknown_cube")],
      contacts: [],
      vectors: [],
    } satisfies VisualFramePayload;
    expect(resolveObjectVisual(frame)?.parts[0]?.half_extents_m).toEqual([0.025, 0.025, 0.025]);
  });
});
