import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { initialRunState, reduceRunEvent, reduceRunEvents, seekRun } from "./run-reducer";

describe("run reducer", () => {
  it("deterministically reduces the complete fixture", () => {
    const first = reduceRunEvents(demoEvents);
    const second = reduceRunEvents(demoEvents);
    expect(first.status).toBe("completed");
    expect(first.taskId).toBe("push-cube-v1");
    expect(first.verification?.success).toBe(true);
    expect(first.worldFrames.truth?.frame.frame_id).toBe(8);
    expect(first.worldFrames.belief?.frame.frame_id).toBe(6);
    expect(first.predictionEvents).toHaveLength(2);
    expect(first.selectedActionId).toBe("push_straight_low");
    expect(first.activeScene).toBeNull();
    expect(first.lastSequence).toBe(demoEvents.length);
    expect(first.beliefs.effective_static_friction?.mean).toBeCloseTo(.55);
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

  it("replays the admitted graph and its recorded node progress", () => {
    const compiled = demoEvents.find((event) => event.type === "action.graph_compiled")!;
    const pushEntered = demoEvents.find(
      (event) => event.type === "action.node_transitioned" && event.payload.node_id === "motion_01_contact_push" && event.payload.state === "entered",
    )!;
    const beforeGraph = seekRun(demoEvents, compiled.sequence - 1);
    const duringPush = seekRun(demoEvents, pushEntered.sequence);
    expect(beforeGraph.actionGraph).toBeNull();
    expect(duringPush.actionGraphId).toBe(compiled.payload.graph_id);
    expect(duringPush.activeActionNodeId).toBe("motion_01_contact_push");
    expect(duringPush.actionNodeStates.motion_00_contact_approach).toBe("completed");
    expect(reduceRunEvents(demoEvents).actionNodeStates.terminal_completed).toBe("completed");
  });

  it("flags transitions outside the admitted graph without changing node progress", () => {
    const compiledIndex = demoEvents.findIndex((event) => event.type === "action.graph_compiled");
    const admitted = reduceRunEvents(demoEvents.slice(0, compiledIndex + 1));
    const transition = demoEvents.find((event) => event.type === "action.node_transitioned")!;
    const invalid = {
      ...transition,
      event_id: "wrong-graph-transition",
      sequence: admitted.lastSequence + 1,
      payload: { ...transition.payload, graph_id: `sha256:${"9".repeat(64)}` },
    };
    const rejected = reduceRunEvent(admitted, invalid);
    expect(rejected.actionGraphContractError).toMatch(/does not match/i);
    expect(rejected.activeActionNodeId).toBeNull();
  });

  it("retains independent Reality and Twin frames across interleaved events and seek", () => {
    const beliefEvent = demoEvents.find(
      (event) => event.type === "simulation.visual_frame" && event.payload.scene === "belief",
    )!;
    const afterBelief = seekRun(demoEvents, beliefEvent.sequence);
    expect(afterBelief.worldFrames.truth?.frame.scene).toBe("truth");
    expect(afterBelief.worldFrames.belief?.frame.scene).toBe("belief");
    expect(afterBelief.activeScene).toBe("belief");

    const complete = reduceRunEvents(demoEvents);
    expect(complete.worldFrames.truth?.sequence).toBeGreaterThan(
      complete.worldFrames.belief?.sequence ?? 0,
    );
    expect(complete.worldFrames.belief?.frame.world_instance_id).toBe("belief-world-demo");
  });

  it("derives the advancing world from stage intent rather than frame arrival", () => {
    const measureEnteredIndex = demoEvents.findIndex(
      (event) => event.type === "stage.entered" && event.stage === "measure",
    );
    const duringMeasure = reduceRunEvents(demoEvents.slice(0, measureEnteredIndex + 1));
    const beliefFrame = demoEvents.find(
      (event) => event.type === "simulation.visual_frame" && event.payload.scene === "belief",
    )!;
    const retainedSnapshot = {
      ...beliefFrame,
      event_id: "retained-belief-snapshot",
      sequence: duringMeasure.lastSequence + 1,
    };
    const afterSnapshot = reduceRunEvent(duringMeasure, retainedSnapshot);
    expect(afterSnapshot.worldFrames.belief?.frame.scene).toBe("belief");
    expect(afterSnapshot.activeScene).toBe("truth");

    const stageCompleted = {
      ...demoEvents.find((event) => event.type === "stage.completed" && event.stage === "measure")!,
      event_id: "measure-stage-completed",
      sequence: afterSnapshot.lastSequence + 1,
    };
    expect(reduceRunEvent(afterSnapshot, stageCompleted).activeScene).toBeNull();
  });
});
