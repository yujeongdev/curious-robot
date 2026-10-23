import { useState } from "react";
import type { RunState, WorldScene } from "../state/run-reducer";
import { NewtonViewport } from "./newton-viewport";

export function isSceneAdvancing(run: RunState, scene: WorldScene): boolean {
  if (run.activeScene !== scene || !run.activeStageInstanceId) return false;
  const activeStage = run.stageInstances.find((item) => item.id === run.activeStageInstanceId);
  const frame = run.worldFrames[scene];
  return Boolean(activeStage && frame && frame.sequence > activeStage.firstSequence);
}

export function DualNewtonWorkspace({
  run,
  disconnected = false,
}: {
  run: RunState;
  disconnected?: boolean;
}) {
  const [mobileWorld, setMobileWorld] = useState<"truth" | "belief">("truth");
  const reality = run.worldFrames.truth;
  const twin = run.worldFrames.belief;
  const terminal = ["completed", "failed", "cancelled", "abandoned"].includes(run.status);
  return (
    <section className="dual-newton-workspace" aria-label="Reality and learned Twin comparison">
      <div className="mobile-world-switch" role="group" aria-label="Displayed physics world">
        <button type="button" aria-pressed={mobileWorld === "truth"} onClick={() => setMobileWorld("truth")}>Reality stand-in</button>
        <button type="button" aria-pressed={mobileWorld === "belief"} onClick={() => setMobileWorld("belief")}>Learned Twin</button>
      </div>
      <div className="dual-world-panes">
        <div className="world-pane-slot" data-mobile-visible={mobileWorld === "truth"}>
          <NewtonViewport
            scene="truth"
            title="Physical experiment & fresh deployment"
            frame={reality?.frame ?? null}
            frameSequence={reality?.sequence}
            active={isSceneAdvancing(run, "truth")}
            heldMessage={terminal ? "Terminal Reality frame retained for audit" : run.activeScene === "belief" ? "Held while the Twin evaluates a candidate" : undefined}
            disconnected={disconnected}
          />
        </div>
        <div className="world-pane-slot" data-mobile-visible={mobileWorld === "belief"}>
          <NewtonViewport
            scene="belief"
            title="Estimated physics · calibration & prediction"
            frame={twin?.frame ?? null}
            frameSequence={twin?.sequence}
            active={isSceneAdvancing(run, "belief")}
            heldMessage={terminal ? "Last learned Twin frame retained for comparison" : run.activeScene === "truth" ? "Held while Reality collects evidence or deploys" : undefined}
            disconnected={disconnected}
          />
        </div>
      </div>
    </section>
  );
}
