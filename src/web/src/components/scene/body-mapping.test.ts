import { describe, expect, it } from "vitest";
import type { BodyTransform } from "../../generated/agent-events";
import { findCubeBody, isFrankaBody } from "./body-mapping";

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
});
