import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { parseAgentEvent } from "./agent-events";

describe("agent event boundary", () => {
  it("accepts all fixture events", () => {
    expect(demoEvents.map(parseAgentEvent)).toHaveLength(demoEvents.length);
  });

  it("rejects incompatible schema majors", () => {
    expect(() => parseAgentEvent({ ...demoEvents[0], schema_version: "2.0.0" })).toThrow(/schema 1/i);
  });

  it("rejects malformed authoritative visual frames", () => {
    const visual = demoEvents.find((event) => event.type === "simulation.visual_frame")!;
    expect(() => parseAgentEvent({ ...visual, payload: { ...visual.payload, bodies: [{ body_id: "cube" }] } })).toThrow();
  });

  it("accepts versioned public compound geometry without physical parameters", () => {
    const visual = demoEvents.find((event) => event.type === "simulation.visual_frame")!;
    const parsed = parseAgentEvent({
      ...visual,
      payload: {
        ...visual.payload,
        bodies: [
          ...(visual.payload.bodies as unknown[]),
          { body_id: "unknown_t_bar", label: "unknown_t_bar", asset_node_id: "t-bar", position_m: [0, 0, 0.02], orientation_xyzw: [0, 0, 0, 1] },
        ],
        object_geometry: {
          archetype_id: "t-bar",
          archetype_version: "1.0.0",
          geometry_hash: `sha256:${"a".repeat(64)}`,
          body_id: "unknown_t_bar",
          parts: [
            { part_id: "stem", center_object_m: [-0.01, 0, 0], half_extents_m: [0.04, 0.01, 0.02] },
            { part_id: "crossbar", center_object_m: [0.04, 0, 0], half_extents_m: [0.01, 0.05, 0.02] },
          ],
        },
      },
    });
    expect(parsed.payload.object_geometry).not.toHaveProperty("mass_kg");
    expect(() => parseAgentEvent({
      ...visual,
      payload: {
        ...visual.payload,
        object_geometry: {
          archetype_id: "t-bar",
          archetype_version: "1.0.0",
          geometry_hash: `sha256:${"a".repeat(64)}`,
          body_id: "missing-body",
          parts: [{ part_id: "stem", center_object_m: [0, 0, 0], half_extents_m: [0.04, 0.01, 0.02] }],
        },
      },
    })).toThrow(/body must exist/i);
  });
});
