import { Link, useNavigate } from "@tanstack/react-router";
import { CircleDot, Download, Radio, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { commandRun, subscribeToRun } from "../api/events";
import { EvidenceFeed } from "../components/evidence-feed";
import { NewtonViewport } from "../components/newton-viewport";
import { RunControls } from "../components/run-controls";
import { StageStepper } from "../components/stage-stepper";
import { TelemetryDock } from "../components/telemetry-dock";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Progress } from "../components/ui/progress";
import { demoEvents } from "../fixtures/demo-run";
import { formatElapsed } from "../lib/utils";
import { useRunStore } from "../state/run-store";

export function RunRoute({ runId, fixture }: { runId: string; fixture?: boolean }) {
  const navigate = useNavigate();
  const run = useRunStore((state) => state.run);
  const ingest = useRunStore((state) => state.ingest);
  const loadReplay = useRunStore((state) => state.loadReplay);
  const seek = useRunStore((state) => state.seek);
  const resetStore = useRunStore((state) => state.reset);
  const sourceEvents = useRunStore((state) => state.sourceEvents);
  const mode = useRunStore((state) => state.mode);
  const selectedSequence = useRunStore((state) => state.selectedSequence);
  const [connection, setConnection] = useState<"connecting" | "open" | "error">("connecting");
  const [fixturePlaying, setFixturePlaying] = useState(true);
  const cursor = useRef(0);
  const isFixture = fixture || runId === "demo";

  useEffect(() => {
    resetStore();
    cursor.current = 0;
    if (isFixture) {
      queueMicrotask(() => setConnection("open"));
      return;
    }
    return subscribeToRun(runId, 0, ingest, setConnection);
  }, [ingest, isFixture, resetStore, runId]);

  useEffect(() => {
    if (!isFixture || !fixturePlaying || mode === "replay") return;
    const timer = window.setInterval(() => {
      const next = demoEvents[cursor.current];
      if (!next) {
        setFixturePlaying(false);
        return;
      }
      ingest(next);
      cursor.current += 1;
    }, 260);
    return () => window.clearInterval(timer);
  }, [fixturePlaying, ingest, isFixture, mode]);

  const setMode = (nextMode: "live" | "replay") => {
    if (nextMode === "replay") {
      setFixturePlaying(false);
      loadReplay(isFixture ? demoEvents : sourceEvents);
      seek(1);
    } else {
      resetStore();
      cursor.current = 0;
      setFixturePlaying(true);
    }
  };

  const control = useCallback(async (command: "pause" | "resume" | "step" | "cancel") => {
    if (isFixture) {
      if (command === "pause") setFixturePlaying(false);
      if (command === "resume") setFixturePlaying(true);
      if (command === "step") {
        const next = demoEvents[cursor.current];
        if (next) { ingest(next); cursor.current += 1; }
      }
      if (command === "cancel") setFixturePlaying(false);
      return;
    }
    await commandRun(runId, command);
  }, [ingest, isFixture, runId]);

  const controlStatus = isFixture && !fixturePlaying && run.status === "running" ? "paused" : run.status;
  const progress = Math.round((run.lastSequence / Math.max(1, isFixture ? demoEvents.length : run.lastSequence + 8)) * 100);
  return (
    <main className="agent-lab">
      <header className="lab-header">
        <Link to="/runs/new" className="brand"><span className="brand-mark">CR</span><span>Curious Robot<small>Agent Lab</small></span></Link>
        <div className="run-identity">
          <span className="eyebrow">Run</span>
          <strong className="mono">{runId === "demo" ? "DEMO-0007" : runId.slice(0, 12)}</strong>
          <span className="mono metadata">seed 7 · cuda:0</span>
        </div>
        <div className="mode-tabs" role="tablist" aria-label="Workspace mode">
          <button role="tab" aria-selected={mode === "live"} onClick={() => setMode("live")}><Radio /> Live</button>
          <button role="tab" aria-selected={mode === "replay"} onClick={() => setMode("replay")}><CircleDot /> Replay</button>
        </div>
        <div className="run-state">
          <Badge className={`status-badge status-badge--${controlStatus}`}>{controlStatus.toUpperCase()}</Badge>
          <span className="mono">SIM {formatElapsed(run.lastSimTimeS)}</span>
          {connection === "error" && <span className="connection-error"><TriangleAlert /> viewer disconnected</span>}
        </div>
        <RunControls status={controlStatus} onCommand={control} onReset={() => navigate({ to: "/runs/new" })} />
      </header>
      <Progress value={progress} label="Run progress" />
      {(run.status === "preparing" || run.status === "idle") && sourceEvents.length > 0 && (
        <div className="system-banner"><span className="spin-dot" /> Preparing Newton · validating assets and body manifest</div>
      )}
      {connection === "error" && <div className="degraded-banner" role="status">Visual connection interrupted. Committed evidence remains available; no run state has been fabricated.</div>}
      <StageStepper run={run} />
      <div className="workspace-grid">
        <NewtonViewport frame={run.latestFrame} disconnected={connection === "error"} />
        <EvidenceFeed run={run} />
      </div>
      <TelemetryDock run={run} />
      {mode === "replay" && (
        <div className="replay-bar" aria-label="Replay controls">
          <Button size="compact" variant="ghost" onClick={() => seek(Math.max(1, (selectedSequence ?? 1) - 1))}>− frame</Button>
          <input aria-label="Replay sequence" type="range" min={1} max={Math.max(1, sourceEvents.length)} value={selectedSequence ?? 1} onChange={(event) => seek(Number(event.target.value))} />
          <span className="mono">SEQ {selectedSequence ?? 0} / {sourceEvents.length}</span>
          <Button size="compact" variant="ghost"><Download /> Artifacts</Button>
        </div>
      )}
      <div className="mobile-safety-bar"><RunControls status={controlStatus} onCommand={control} /></div>
      <div className="sr-only" aria-live="assertive">{run.status === "failed" ? run.fatalError ?? "Run failed safely" : ""}</div>
    </main>
  );
}
