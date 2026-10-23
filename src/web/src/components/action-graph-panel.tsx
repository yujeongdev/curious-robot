import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import { Check, ChevronDown, Circle, CircleDot, GitBranch, ShieldAlert } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { ActionGraphNode } from "../generated/agent-events";
import type { ActionNodeState, RunState } from "../state/run-reducer";
import { Badge } from "./ui/badge";

function nodeLabel(node: ActionGraphNode): string {
  const phase = node.parameters.phase;
  if (typeof phase === "string") return phase.replaceAll("_", " ");
  if (node.kind === "observe") return "Observe object pose";
  if (node.kind === "guard") return "Validate observation";
  if (node.kind === "verify") return "Verify goal";
  if (node.kind === "safe_stop") return "Safe stop";
  if (node.node_id === "terminal_completed") return "Complete";
  if (node.node_id === "terminal_return_to_agent") return "Return to agent";
  return node.node_id.replaceAll("_", " ");
}

function stateLabel(state: ActionNodeState): string {
  const labels: Record<ActionNodeState, string> = {
    pending: "Waiting",
    entered: "Running",
    completed: "Complete",
    failed: "Failed",
    safety_trip: "Safety trip",
    goal_miss: "Goal miss",
  };
  return labels[state];
}

function StateIcon({ state }: { state: ActionNodeState }) {
  if (state === "completed") return <Check aria-hidden="true" />;
  if (state === "entered") return <CircleDot aria-hidden="true" />;
  if (["failed", "safety_trip", "goal_miss"].includes(state)) return <ShieldAlert aria-hidden="true" />;
  return <Circle aria-hidden="true" />;
}

function successPath(run: RunState): ActionGraphNode[] {
  if (!run.actionGraph) return [];
  const byId = new Map(run.actionGraph.nodes.map((node) => [node.node_id, node]));
  const success = new Map(
    run.actionGraph.edges
      .filter((edge) => edge.guard === "on_success")
      .map((edge) => [edge.source_node_id, edge.target_node_id]),
  );
  const path: ActionGraphNode[] = [];
  const visited = new Set<string>();
  let nodeId: string | undefined = run.actionGraph.entry_node_id;
  while (nodeId && !visited.has(nodeId)) {
    visited.add(nodeId);
    const node = byId.get(nodeId);
    if (!node) break;
    path.push(node);
    nodeId = success.get(nodeId);
  }
  return path;
}

function NodeRow({ node, state, compact = false }: { node: ActionGraphNode; state: ActionNodeState; compact?: boolean }) {
  return (
    <li className="action-node" data-state={state} aria-current={state === "entered" ? "step" : undefined}>
      <span className="action-node-state"><StateIcon state={state} /><span className="sr-only">{stateLabel(state)}</span></span>
      <span className="action-node-copy">
        <strong>{nodeLabel(node)}</strong>
        {!compact && <small>{node.authority} · {node.control_mode?.replaceAll("_", " ") ?? node.executor.replaceAll("_", " ")} · {node.timeout_s.toFixed(node.timeout_s < .01 ? 3 : 1)} s</small>}
      </span>
      <b>{stateLabel(state)}</b>
    </li>
  );
}

export function ActionGraphPanel({ run, compact = false }: { run: RunState; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const path = useMemo(() => successPath(run), [run]);
  if (!run.actionGraph || !run.actionGraphId) {
    return (
      <section className="action-graph action-graph--empty" aria-labelledby={titleId}>
        <header>
          <div><span className="eyebrow">Deterministic execution</span><h2 id={titleId}>Action graph</h2></div>
          <Badge>NOT ADMITTED</Badge>
        </header>
        <p>The selected policy has not published an execution graph yet.</p>
      </section>
    );
  }

  const currentId = run.activeActionNodeId ?? run.lastActionNodeId ?? run.actionGraph.entry_node_id;
  const currentIndex = path.findIndex((node) => node.node_id === currentId);
  const currentNode = run.actionGraph.nodes.find((node) => node.node_id === currentId) ?? path[0]!;
  const visible = currentIndex >= 0
    ? path.slice(Math.max(0, currentIndex - 1), currentIndex + 2)
    : [currentNode];
  const hasTrace = Object.values(run.actionNodeStates).some((state) => state !== "pending");
  const graphShort = `${run.actionGraphId.slice(0, 13)}…`;

  return (
    <section className="action-graph" aria-labelledby={titleId}>
      <header>
        <div>
          <span className="eyebrow">Deterministic execution</span>
          <h2 id={titleId}>Action graph</h2>
        </div>
        <Badge>{hasTrace ? "RECORDED TRACE" : "CONTRACT ONLY"}</Badge>
      </header>
      {run.actionGraphContractError && <p className="action-graph-error" role="alert">{run.actionGraphContractError}</p>}
      <div className="action-graph-summary">
        <div>
          <span className="mono">{currentIndex >= 0 ? `NODE ${currentIndex + 1} / ${path.length}` : "TERMINAL BRANCH"}</span>
          <strong>{nodeLabel(currentNode)}</strong>
        </div>
        <span className="mono">{run.actionGraph.wall_clock_deadline_s.toFixed(1)} s deadline</span>
      </div>
      <ol className="action-graph-window" aria-label="Current execution path">
        {visible.map((node) => <NodeRow key={node.node_id} node={node} state={run.actionNodeStates[node.node_id] ?? "pending"} compact />)}
      </ol>
      <div className="action-graph-safety"><ShieldAlert aria-hidden="true" /><span>Any failure or safety trip</span><strong>Safe stop</strong></div>
      {!compact && (
        <CollapsiblePrimitive.Root open={open} onOpenChange={setOpen}>
          <CollapsiblePrimitive.Trigger className="action-graph-trigger">
            <GitBranch aria-hidden="true" /> {open ? "Hide full graph" : "Inspect full graph"} <ChevronDown aria-hidden="true" />
          </CollapsiblePrimitive.Trigger>
          <CollapsiblePrimitive.Content className="action-graph-details">
            <ol aria-label="Full action graph success path">
              {path.map((node) => <NodeRow key={node.node_id} node={node} state={run.actionNodeStates[node.node_id] ?? "pending"} />)}
            </ol>
            <div className="action-graph-branches">
              <p><strong>Goal miss</strong><span>Verify goal → Return to agent</span></p>
              <p><strong>Failure / safety</strong><span>Operational node → Safe stop</span></p>
            </div>
            <dl className="action-graph-contract">
              <div><dt>Graph</dt><dd className="mono">{graphShort}</dd></div>
              <div><dt>Schema</dt><dd className="mono">{run.actionGraph.graph_schema_version}</dd></div>
              <div><dt>Compiler</dt><dd className="mono">{run.actionGraph.compiler_version}</dd></div>
            </dl>
          </CollapsiblePrimitive.Content>
        </CollapsiblePrimitive.Root>
      )}
    </section>
  );
}
