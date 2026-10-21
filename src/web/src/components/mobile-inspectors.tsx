import { Activity, ListTree } from "lucide-react";
import type { RunState } from "../state/run-reducer";
import { EvidenceFeed } from "./evidence-feed";
import { TelemetryDock } from "./telemetry-dock";
import { Button } from "./ui/button";
import { Sheet } from "./ui/sheet";

export function MobileInspectors({ run }: { run: RunState }) {
  return (
    <div className="mobile-inspectors" aria-label="Mobile inspection panels">
      <Sheet title="Telemetry" trigger={<Button variant="secondary"><Activity /> Telemetry</Button>}><TelemetryDock run={run} /></Sheet>
      <Sheet title="Evidence history" trigger={<Button variant="secondary"><ListTree /> Evidence history</Button>}><EvidenceFeed run={run} /></Sheet>
    </div>
  );
}
