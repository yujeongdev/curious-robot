import { CircleStop, Pause, Play, RotateCcw, StepForward } from "lucide-react";
import type { RunStatus } from "../state/run-reducer";
import { Button } from "./ui/button";

interface Props {
  status: RunStatus;
  onCommand: (command: "pause" | "resume" | "step" | "cancel") => void;
  onReset?: () => void;
}

export function RunControls({ status, onCommand, onReset }: Props) {
  const running = status === "running" || status === "preparing";
  const paused = status === "paused";
  const terminal = ["completed", "failed", "cancelled", "abandoned"].includes(status);
  return (
    <div className="run-controls" aria-label="Run controls">
      {running ? (
        <Button size="compact" onClick={() => onCommand("pause")}><Pause /> Pause</Button>
      ) : (
        <Button size="compact" onClick={() => onCommand("resume")} disabled={!paused}><Play /> Resume</Button>
      )}
      <Button size="compact" onClick={() => onCommand("step")} disabled={!paused} className="desktop-only"><StepForward /> Step</Button>
      <Button size="compact" variant="danger" onClick={() => onCommand("cancel")} disabled={terminal || status === "idle"}><CircleStop /> Safe stop</Button>
      {terminal && onReset && <Button size="compact" variant="ghost" onClick={onReset}><RotateCcw /> New run</Button>}
    </div>
  );
}
