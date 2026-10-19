import { Check, ChevronLeft, ChevronRight, Circle, CircleDashed, X } from "lucide-react";
import type { DetailedStage } from "../generated/agent-events";
import type { RunState, StageStatus } from "../state/run-reducer";
import { useRunStore } from "../state/run-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export const macroPhases = [
  { id: "observe", label: "Observe", stages: ["observe"] },
  { id: "discover", label: "Discover", stages: ["reason", "identify_unknowns", "design_experiment"] },
  { id: "experiment", label: "Experiment", stages: ["act", "measure"] },
  { id: "calibrate", label: "Calibrate", stages: ["system_identification", "update_simulation"] },
  { id: "decide", label: "Decide", stages: ["predict", "select_action"] },
  { id: "execute", label: "Execute", stages: ["execute", "verify", "learn"] },
] as const satisfies readonly { id: string; label: string; stages: readonly DetailedStage[] }[];

function containsStage(stages: readonly DetailedStage[], stage: DetailedStage): boolean {
  return stages.some((candidate) => candidate === stage);
}

function phaseStatus(run: RunState, stages: readonly DetailedStage[]): StageStatus {
  const matching = run.stageInstances.filter((item) => containsStage(stages, item.stage));
  if (matching.some((item) => item.status === "active")) return "active";
  if (matching.some((item) => item.status === "failed")) return "failed";
  if (matching.length > 0 && matching.every((item) => item.status === "completed" || item.status === "skipped")) return "completed";
  if (matching.length > 0 && matching.every((item) => item.status === "skipped")) return "skipped";
  return "pending";
}

function StatusIcon({ status }: { status: StageStatus }) {
  if (status === "completed") return <Check aria-hidden="true" />;
  if (status === "failed") return <X aria-hidden="true" />;
  if (status === "active") return <Circle className="phase-pulse" aria-hidden="true" />;
  if (status === "skipped") return <CircleDashed aria-hidden="true" />;
  return <Circle aria-hidden="true" />;
}

export function StageStepper({ run }: { run: RunState }) {
  const inspect = useRunStore((state) => state.inspect);
  const activeInstance = run.stageInstances.find((item) => item.id === run.activeStageInstanceId);
  const displayInstance = activeInstance ?? run.stageInstances.at(-1);
  const resolvedIndex = macroPhases.findIndex((phase) => displayInstance && containsStage(phase.stages, displayInstance.stage));
  const activeIndex = resolvedIndex >= 0 ? resolvedIndex : run.status === "completed" ? macroPhases.length - 1 : 0;

  const activate = (index: number) => {
    const phase = macroPhases[index];
    if (!phase) return;
    const instance = run.stageInstances.find((item) => containsStage(phase.stages, item.stage));
    if (instance) inspect(instance.firstSequence);
  };

  return (
    <nav className="stage-stepper" aria-label="Discovery phases">
      <div className="phase-mobile">
        <Button size="icon" variant="ghost" aria-label="Previous phase" onClick={() => activate(activeIndex - 1)} disabled={activeIndex === 0}>
          <ChevronLeft />
        </Button>
        <div aria-live="polite">
          <span className="eyebrow">Current phase</span>
          <strong>{macroPhases[activeIndex]?.label ?? "Observe"}</strong>
          <span>{displayInstance ? (run.status === "completed" ? "Run complete" : displayInstance.stage.replaceAll("_", " ")) : "Not started"}</span>
        </div>
        <Button size="icon" variant="ghost" aria-label="Next phase" onClick={() => activate(activeIndex + 1)} disabled={activeIndex >= macroPhases.length - 1}>
          <ChevronRight />
        </Button>
      </div>
      <ol className="phase-list">
        {macroPhases.map((phase, index) => {
          const status = phaseStatus(run, phase.stages);
          const instances = run.stageInstances.filter((item) => containsStage(phase.stages, item.stage));
          const current = instances.find((item) => item.status === "active");
          const iteration = Math.max(0, ...instances.map((item) => item.iteration));
          return (
            <li key={phase.id} data-status={status}>
              <button
                type="button"
                onClick={() => activate(index)}
                disabled={instances.length === 0}
                aria-current={status === "active" ? "step" : undefined}
                aria-label={`${phase.label}: ${instances.length ? instances.map((item) => item.stage.replaceAll("_", " ")).join(", ") : "not started"}`}
              >
                <span className="phase-index"><StatusIcon status={status} /> {String(index + 1).padStart(2, "0")}</span>
                <strong>{phase.label}</strong>
                <span>{current?.stage.replaceAll("_", " ") ?? (status === "completed" ? "Complete" : "Waiting")}</span>
                {iteration > 0 && <Badge>Experiment {iteration}</Badge>}
              </button>
            </li>
          );
        })}
      </ol>
      {run.gap && <div className="connection-marker" role="status">Connection interrupted · expected {run.gap.expected}, received {run.gap.received}</div>}
    </nav>
  );
}
