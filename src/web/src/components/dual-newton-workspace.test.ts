import { describe, expect, it } from "vitest";
import { demoEvents } from "../fixtures/demo-run";
import { reduceRunEvents } from "../state/run-reducer";
import { isSceneAdvancing } from "./dual-newton-workspace";

describe("DualNewtonWorkspace world activity", () => {
  it("waits for a frame newer than the active stage before showing ADVANCING", () => {
    const firstBeliefFrame = demoEvents.findIndex(
      (event) => event.type === "simulation.visual_frame" && event.payload.scene === "belief",
    );
    const firstTwin = reduceRunEvents(demoEvents.slice(0, firstBeliefFrame + 1));
    expect(isSceneAdvancing(firstTwin, "belief")).toBe(true);

    const predictEntered = demoEvents.findIndex(
      (event, index) => index > firstBeliefFrame && event.type === "stage.entered" && event.stage === "predict",
    );
    const rebuilding = reduceRunEvents(demoEvents.slice(0, predictEntered + 1));
    expect(rebuilding.worldFrames.belief?.frame.frame_id).toBe(6);
    expect(rebuilding.activeScene).toBe("belief");
    expect(isSceneAdvancing(rebuilding, "belief")).toBe(false);
  });
});
