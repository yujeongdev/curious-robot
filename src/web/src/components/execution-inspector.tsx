import type { RunState } from "../state/run-reducer";
import { ActionGraphPanel } from "./action-graph-panel";
import { EvidenceFeed } from "./evidence-feed";

export function ExecutionInspector({ run }: { run: RunState }) {
  return (
    <aside className="execution-inspector" aria-label="Execution and evidence inspector">
      <ActionGraphPanel run={run} />
      <EvidenceFeed run={run} />
    </aside>
  );
}
