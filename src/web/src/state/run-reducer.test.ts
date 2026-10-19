import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { initialRunState, reduceRunEvent, reduceRunEvents, seekRun } from "./run-reducer";

describe("run reducer", () => {
  it("deterministically reduces the complete fixture", () => {
    const first = reduceRunEvents(demoEvents);
    const second = reduceRunEvents(demoEvents);
    expect(first.status).toBe("completed");
    expect(first.verification?.success).toBe(true);
    expect(first.latestFrame?.frame_id).toBe(8);
    expect(first.lastSequence).toBe(demoEvents.length);
    expect(first.beliefs.effective_static_friction?.mean).toBeCloseTo(.83);
    expect(second).toEqual(first);
  });

  it("ignores identical duplicate event IDs", () => {
    const once = reduceRunEvent(initialRunState, demoEvents[0]!);
    expect(reduceRunEvent(once, demoEvents[0]!)).toBe(once);
  });

  it("detects sequence gaps without advancing state", () => {
    const first = reduceRunEvent(initialRunState, demoEvents[0]!);
    const gap = reduceRunEvent(first, demoEvents[2]!);
    expect(gap.lastSequence).toBe(1);
    expect(gap.gap).toEqual({ expected: 2, received: 3 });
    expect(gap.stageInstances).toHaveLength(0);
  });

  it("replays backwards through the same reducer", () => {
    const target = demoEvents.find((event) => event.type === "belief.updated" && event.payload.revision === 2)!;
    const state = seekRun(demoEvents, target.sequence);
    expect(state.lastSequence).toBe(target.sequence);
    expect(state.events.at(-1)?.event_id).toBe(target.event_id);
    expect(state.status).toBe("running");
  });
});
