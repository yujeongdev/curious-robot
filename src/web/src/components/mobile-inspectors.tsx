import { Activity, GitBranch } from "lucide-react";
import type { ReactNode } from "react";
import type { RunState } from "../state/run-reducer";
import type { RunStatus } from "../state/run-reducer";
import { EvidenceFeed } from "./evidence-feed";
import { ActionGraphPanel } from "./action-graph-panel";
import { TelemetryDock } from "./telemetry-dock";
import { Button } from "./ui/button";
import { Sheet } from "./ui/sheet";
import { RunControls } from "./run-controls";

type RunCommand = "pause" | "resume" | "step" | "cancel";

function InspectorBody({ children, status, onCommand }: { children: ReactNode; status: RunStatus; onCommand: (command: RunCommand) => void }) {
  return <div className="mobile-inspector-body"><div>{children}</div><div className="mobile-sheet-controls"><RunControls status={status} onCommand={onCommand} /></div></div>;
}

export function MobileInspectors({ run, status, onCommand }: { run: RunState; status: RunStatus; onCommand: (command: RunCommand) => void }) {
  return (
    <div className="mobile-inspectors" aria-label="Mobile inspection panels">
      <Sheet title="Telemetry" trigger={<Button variant="secondary"><Activity /> Telemetry</Button>}>
        <InspectorBody status={status} onCommand={onCommand}><TelemetryDock run={run} /></InspectorBody>
      </Sheet>
      <Sheet title="Plan and evidence" trigger={<Button variant="secondary"><GitBranch /> Plan & evidence</Button>}>
        <InspectorBody status={status} onCommand={onCommand}><div className="mobile-plan-inspector"><ActionGraphPanel run={run} /><EvidenceFeed run={run} /></div></InspectorBody>
      </Sheet>
    </div>
  );
}
