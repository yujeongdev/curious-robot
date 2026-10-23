import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { actionGraphCompiledPayloadSchema, actionNodeTransitionPayloadSchema, parseAgentEvent, visualFramePayloadSchema } from "./agent-events";

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

  it("validates graph commitments and node transitions", () => {
    const compiled = demoEvents.find((event) => event.type === "action.graph_compiled")!;
    const transition = demoEvents.find((event) => event.type === "action.node_transitioned")!;
    expect(actionGraphCompiledPayloadSchema.parse(compiled.payload).graph.nodes.length).toBeGreaterThan(1);
    expect(actionNodeTransitionPayloadSchema.parse(transition.payload).node_id).toBe("observe_initial_object_pose");
    expect(() => actionGraphCompiledPayloadSchema.parse({ ...compiled.payload, graph_id: "not-a-hash" })).toThrow();
    expect(() => actionNodeTransitionPayloadSchema.parse({ ...transition.payload, state: "teleported" })).toThrow();
  });

  it("keeps the synthetic graph commitment content-addressed", async () => {
    const compiled = demoEvents.find((event) => event.type === "action.graph_compiled")!;
    const canonicalize = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(canonicalize);
      if (value !== null && typeof value === "object") {
        return Object.fromEntries(
          Object.entries(value as Record<string, unknown>)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, child]) => [key, canonicalize(child)]),
        );
      }
      return value;
    };
    const bytes = new TextEncoder().encode(JSON.stringify(canonicalize(compiled.payload.graph)));
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");
    expect(compiled.payload.graph_id).toBe(`sha256:${digest}`);
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

  it("accepts an exact public T-bar target silhouette without a body binding", () => {
    const visual = demoEvents.find((event) => event.type === "simulation.visual_frame")!;
    const geometry = {
      archetype_id: "t-bar",
      archetype_version: "1.0.0",
      geometry_hash: `sha256:${"b".repeat(64)}`,
      parts: [
        { part_id: "stem", center_object_m: [-0.0125, 0, 0], half_extents_m: [0.0375, 0.0125, 0.02] },
        { part_id: "crossbar", center_object_m: [0.0375, 0, 0], half_extents_m: [0.0125, 0.05, 0.02] },
      ],
    };
    const parsed = visualFramePayloadSchema.parse({
      ...visual.payload,
      bodies: [
        ...(visual.payload.bodies as unknown[]),
        { body_id: "unknown_t_bar", label: "unknown_t_bar", asset_node_id: "target-test-tbar", position_m: [0, 0, 0.02], orientation_xyzw: [0, 0, 0, 1] },
      ],
      object_geometry: { ...geometry, body_id: "unknown_t_bar" },
      target_region: {
        center_m: [0.15, 0, 0.002],
        radius_m: 0.03,
        yaw_rad: 0,
        yaw_tolerance_rad: Math.PI / 20,
        geometry,
      },
    });
    expect(parsed.target_region?.geometry?.parts).toHaveLength(2);
    expect(() => visualFramePayloadSchema.parse({
      ...parsed,
      target_region: {
        ...parsed.target_region,
        geometry: { ...parsed.target_region?.geometry, geometry_hash: `sha256:${"c".repeat(64)}` },
      },
    })).toThrow(/must exactly match/i);
  });
});
