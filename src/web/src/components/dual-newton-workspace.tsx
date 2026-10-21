import { useState, type ReactNode } from "react";
import type { RunState, WorldScene } from "../state/run-reducer";
import { NewtonViewport } from "./newton-viewport";

type WorldFlow = "experiment" | "posterior" | "validate" | "deploy" | "complete";

function FlowStep({ current, children }: { current: boolean; children: ReactNode }) {
  return (
    <span data-active={current} aria-current={current ? "step" : undefined}>
      {children}
      {current && <b className="flow-current" aria-hidden="true">CURRENT</b>}
    </span>
  );
}

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
  const activeStage = run.stageInstances.find((item) => item.id === run.activeStageInstanceId)?.stage;
  const activeFlow: WorldFlow | null = activeStage && ["act", "measure"].includes(activeStage)
    ? "experiment"
    : activeStage && ["system_identification", "update_simulation"].includes(activeStage)
      ? "posterior"
      : activeStage && ["predict", "select_action"].includes(activeStage)
        ? "validate"
        : activeStage && ["execute", "verify", "learn"].includes(activeStage)
          ? "deploy"
          : terminal
            ? "complete"
            : null;
  return (
    <section className="dual-newton-workspace" aria-label="Reality and learned Twin comparison">
      <div className="world-flow" aria-label="Reality to Twin to Reality workflow">
        <FlowStep current={activeFlow === "experiment"}>1 · Reality experiment</FlowStep>
        <i aria-hidden="true">→</i>
        <FlowStep current={activeFlow === "posterior"}>2 · Posterior & Twin rebuild</FlowStep>
        <i aria-hidden="true">→</i>
        <FlowStep current={activeFlow === "validate"}>3 · Twin validation</FlowStep>
        <i aria-hidden="true">→</i>
        <FlowStep current={activeFlow === "deploy" || activeFlow === "complete"}>4 · Fresh Reality deployment</FlowStep>
      </div>
      <div className="mobile-world-switch" role="group" aria-label="Displayed physics world">
        <button type="button" aria-pressed={mobileWorld === "truth"} onClick={() => setMobileWorld("truth")}>Reality</button>
        <button type="button" aria-pressed={mobileWorld === "belief"} onClick={() => setMobileWorld("belief")}>Learned Twin</button>
      </div>
      <div className="dual-world-panes">
        <div className="world-pane-slot" data-mobile-visible={mobileWorld === "truth"}>
          <NewtonViewport
            scene="truth"
            title="Experiment & deployment"
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
            title="Calibration & prediction"
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
