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
});
