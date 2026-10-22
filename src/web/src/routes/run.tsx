import { Link, useNavigate } from "@tanstack/react-router";
import { CircleDot, Download, Radio, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { commandRun, subscribeToRun } from "../api/events";
import { EvidenceFeed } from "../components/evidence-feed";
import { DualNewtonWorkspace } from "../components/dual-newton-workspace";
import { MobileInspectors } from "../components/mobile-inspectors";
import { RunControls } from "../components/run-controls";
import { ResizableWorkspace } from "../components/resizable-workspace";
import { StageStepper } from "../components/stage-stepper";
import { TelemetryDock } from "../components/telemetry-dock";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Progress } from "../components/ui/progress";
import { demoEvents, demoPreviewNotice } from "../fixtures/demo-run";
import { parseAgentEvent } from "../generated/agent-events";
import { formatElapsed } from "../lib/utils";
import { useRunStore } from "../state/run-store";

export function RunRoute({ runId, fixture }: { runId: string; fixture?: boolean }) {
  const navigate = useNavigate();
  const run = useRunStore((state) => state.run);
  const ingest = useRunStore((state) => state.ingest);
  const loadReplay = useRunStore((state) => state.loadReplay);
  const seek = useRunStore((state) => state.seek);
  const resetStore = useRunStore((state) => state.reset);
  const returnToLive = useRunStore((state) => state.returnToLive);
  const sourceEvents = useRunStore((state) => state.sourceEvents);
  const mode = useRunStore((state) => state.mode);
  const selectedSequence = useRunStore((state) => state.selectedSequence);
  const [connection, setConnection] = useState<"connecting" | "open" | "error">("connecting");
  const [fixturePlaying, setFixturePlaying] = useState(true);
  const cursor = useRef(0);
  const captureEvents = useMemo(() => {
    if (!window.__CURIOUS_CAPTURE_EVENTS__) return null;
    return window.__CURIOUS_CAPTURE_EVENTS__.map(parseAgentEvent);
  }, []);
  const fixtureEvents = captureEvents ?? demoEvents;
  const isSyntheticPreview = captureEvents === null && (fixture || runId === "demo");
  const isFixture = isSyntheticPreview || captureEvents !== null;

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
      const next = fixtureEvents[cursor.current];
      if (!next) {
        setFixturePlaying(false);
        return;
      }
      ingest(next);
      cursor.current += 1;
    }, 260);
    return () => window.clearInterval(timer);
  }, [fixtureEvents, fixturePlaying, ingest, isFixture, mode]);

  const setMode = (nextMode: "live" | "replay") => {
    if (nextMode === "replay") {
      setFixturePlaying(false);
      loadReplay(isFixture ? fixtureEvents : sourceEvents);
      seek(1);
    } else {
      returnToLive();
      if (isFixture && cursor.current < fixtureEvents.length) setFixturePlaying(true);
    }
  };

  const control = useCallback(async (command: "pause" | "resume" | "step" | "cancel") => {
    if (isFixture) {
      if (command === "pause") setFixturePlaying(false);
      if (command === "resume") setFixturePlaying(true);
      if (command === "step") {
        const next = fixtureEvents[cursor.current];
        if (next) { ingest(next); cursor.current += 1; }
      }
      if (command === "cancel") setFixturePlaying(false);
      return;
    }
    await commandRun(runId, command);
  }, [fixtureEvents, ingest, isFixture, runId]);

  const controlStatus = isFixture && !fixturePlaying && run.status === "running" ? "paused" : run.status;
  const viewerDisconnected = connection === "error" && !["completed", "failed", "cancelled", "abandoned"].includes(run.status);
  const progress = Math.round((run.lastSequence / Math.max(1, isFixture ? fixtureEvents.length : run.lastSequence + 8)) * 100);
  return (
    <main className="agent-lab">
      <header className="lab-header">
        <Link to="/runs/new" className="brand"><span className="brand-mark">CR</span><span>Curious Robot<small>Agent Lab</small></span></Link>
        <div className="run-identity">
          <span className="eyebrow">Run</span>
          <strong className="mono">{isSyntheticPreview ? "SYNTHETIC PREVIEW" : runId.slice(0, 12)}</strong>
          <span className="mono metadata">{isSyntheticPreview ? "illustrative data · no GPU execution" : `${run.taskId ?? "task pending"} · Newton GPU`}</span>
        </div>
        <div className="mode-tabs" role="tablist" aria-label="Workspace mode">
          <button role="tab" aria-selected={mode === "live"} onClick={() => setMode("live")}><Radio /> {isSyntheticPreview ? "Preview" : "Live"}</button>
          <button role="tab" aria-selected={mode === "replay"} onClick={() => setMode("replay")}><CircleDot /> Replay</button>
        </div>
        <div className="run-state">
          <Badge className={`status-badge status-badge--${controlStatus}`}>{controlStatus.toUpperCase()}</Badge>
          <span className="mono">SIM {formatElapsed(run.lastSimTimeS)}</span>
          {viewerDisconnected && <span className="connection-error"><TriangleAlert /> viewer disconnected</span>}
        </div>
        <RunControls status={controlStatus} onCommand={control} onReset={() => navigate({ to: "/runs/new" })} />
      </header>
      <Progress value={progress} label="Run progress" />
      {isSyntheticPreview && <div className="system-banner" role="note" aria-label="Preview provenance">{demoPreviewNotice}</div>}
      {(run.status === "preparing" || run.status === "idle") && sourceEvents.length > 0 && (
        <div className="system-banner"><span className="spin-dot" /> {isSyntheticPreview ? "Loading synthetic preview" : "Preparing Newton · validating assets and body manifest"}</div>
      )}
      {viewerDisconnected && <div className="degraded-banner" role="status">Visual connection interrupted. Committed evidence remains available; no run state has been fabricated.</div>}
      <StageStepper run={run} />
      <ResizableWorkspace
        primary={<DualNewtonWorkspace run={run} disconnected={viewerDisconnected} />}
        secondary={<EvidenceFeed run={run} />}
      />
      <TelemetryDock run={run} />
      <MobileInspectors run={run} />
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
