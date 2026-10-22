import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import { AlertTriangle, Bot, Check, ChevronDown, FlaskConical, Gauge, Wrench } from "lucide-react";
import type { AgentEvent } from "../generated/agent-events";
import type { RunState } from "../state/run-reducer";
import { useRunStore } from "../state/run-store";
import { formatValue } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

function eventTitle(event: AgentEvent): string {
  const payload = event.payload;
  const titles: Partial<Record<AgentEvent["type"], string>> = {
    "run.started": payload.data_provenance === "synthetic_illustration" ? "Synthetic preview loaded" : "Newton worker initializing",
    "stage.entered": `Entered ${event.stage?.replaceAll("_", " ") ?? "stage"}`,
    "stage.completed": `${event.stage?.replaceAll("_", " ") ?? "Stage"} complete`,
    "rationale.published": "Agent rationale",
    "tool.started": `Calling ${String(payload.tool_name ?? "tool")}`,
    "tool.completed": `${String(payload.tool_name ?? "Tool")} completed`,
    "tool.failed": `${String(payload.tool_name ?? "Tool")} failed`,
    "measurement.sampled": "Sensor sample",
    "measurement.summary": "Experimental measurement",
    "belief.updated": `Belief updated · ${String(payload.parameter_name ?? "parameter")}`,
    "simulation.belief_rebuilt": "Belief simulation rebuilt",
    "prediction.completed": `Predicted · ${String(payload.action_id ?? "candidate")}`,
    "action.selected": `Selected ${String(payload.selected_action_id ?? "action")}`,
    "action.executed": "Action executed",
    "verification.completed": payload.success ? "Task verified" : "Verification failed",
    "learning.completed": "Residual learned",
    "safety.triggered": "Safety controller triggered",
    "run.completed": "Run complete",
    "run.failed": "Run failed",
  };
  return titles[event.type] ?? event.type.replaceAll(".", " ");
}

function iconFor(event: AgentEvent) {
  if (event.type.startsWith("tool.")) return <Wrench />;
  if (event.type.startsWith("measurement.")) return <Gauge />;
  if (event.type === "belief.updated") return <FlaskConical />;
  if (event.type === "safety.triggered" || event.type.endsWith("failed")) return <AlertTriangle />;
  if (event.type === "verification.completed" || event.type === "run.completed") return <Check />;
  return <Bot />;
}

const TOOL_LANES = {
  observe_scene: "REALITY",
  observe_object_pose: "REALITY",
  get_robot_state: "REALITY",
  get_tool_wrench: "REALITY",
  move_joint: "REALITY",
  move_cartesian: "REALITY",
  open_gripper: "REALITY",
  close_gripper: "REALITY",
  grasp_object: "REALITY",
  release_object: "REALITY",
  execute_safe_push: "REALITY",
  enable_cartesian_compliance: "REALITY",
  set_desired_force: "REALITY",
  check_force_condition: "REALITY",
  release_force_control: "REALITY",
  weigh_object: "REALITY",
  probe_static_friction: "REALITY",
  probe_dynamic_friction: "REALITY",
  estimate_center_of_mass: "REALITY",
  verify_task_success: "REALITY",
  create_belief_simulation: "TWIN",
  update_physical_parameters: "TWIN",
  simulate_candidate_action: "TWIN",
  evaluate_prediction_error: "TWIN",
  estimate_parameter_uncertainty: "INFERENCE",
  propose_experiment: "INFERENCE",
  evaluate_information_gain: "INFERENCE",
  select_next_action: "INFERENCE",
} as const;

type EventLane = "REALITY" | "TWIN" | "INFERENCE" | "SYSTEM";

export function eventLane(event: AgentEvent): EventLane {
  if (event.type.startsWith("tool.")) {
    const toolName = String(event.payload.tool_name ?? "");
    return TOOL_LANES[toolName as keyof typeof TOOL_LANES] ?? "SYSTEM";
  }
  if (
    event.type.startsWith("measurement.")
    || event.type === "action.executed"
    || event.type === "verification.completed"
    || event.type === "safety.triggered"
  ) return "REALITY";
  if (event.type === "simulation.belief_rebuilt" || event.type === "prediction.completed") {
    return "TWIN";
  }
  if (
    event.type === "belief.updated"
    || event.type === "rationale.published"
    || event.type === "action.selected"
    || event.type === "learning.completed"
  ) return "INFERENCE";
  return "SYSTEM";
}

function EventSummary({ event }: { event: AgentEvent }) {
  const payload = event.payload;
  if (event.type === "rationale.published") return <p>{String(payload.summary)}</p>;
  if (event.type === "belief.updated") {
    const prior = payload.prior as Record<string, unknown>;
    const posterior = payload.posterior as Record<string, unknown>;
    const priorUncertainty = prior?.uncertainty as Record<string, unknown> | undefined;
    const posteriorUncertainty = posterior?.uncertainty as Record<string, unknown> | undefined;
    const priorInterval = priorUncertainty?.interval95 ?? prior?.interval_95;
    const posteriorInterval = posteriorUncertainty?.interval95 ?? posterior?.interval_95;
    const before = Array.isArray(priorInterval) ? priorInterval.join("–") : "—";
    const after = Array.isArray(posteriorInterval) ? posteriorInterval.join("–") : "—";
    return (
      <div className="belief-delta">
        <span><small>Prior 95%</small><strong>{before}</strong></span>
        <span className="delta-arrow">→</span>
        <span><small>Posterior 95%</small><strong>{after}</strong></span>
        <p>{String(payload.method ?? "Bayesian update")}</p>
      </div>
    );
  }
  if (event.type === "measurement.summary") {
    const aggregates = (payload.aggregate_values ?? payload.aggregates) as Record<string, unknown>;
    return <p>Breakaway force <strong className="mono">{formatValue(aggregates?.breakaway_force_n, 2)} N</strong> · {String((payload.quality_flags as string[])?.join(", ") ?? "validated")}</p>;
  }
  if (event.type === "prediction.completed") {
    return <p>Target error <strong className="mono">{formatValue(payload.target_error_m, 3)} m</strong> · success <strong className="mono">{Math.round(Number(payload.success_probability) * 100)}%</strong></p>;
  }
  if (event.type === "verification.completed") {
    const maximumCornerError = payload.maximum_registered_corner_error_m;
    const rmsCornerError = payload.rms_registered_corner_error_m;
    const hasShapeError = (
      typeof maximumCornerError === "number"
      && Number.isFinite(maximumCornerError)
      && typeof rmsCornerError === "number"
      && Number.isFinite(rmsCornerError)
    );
    return <p>
      {payload.success ? "All goal predicates passed." : "Goal predicate failed."} Residual <strong className="mono">{formatValue(payload.prediction_residual_m, 3)} m</strong>.
      {hasShapeError ? <>
        {" "}Shape max <strong className="mono">{formatValue(maximumCornerError * 1000, 1)} mm</strong> · RMS <strong className="mono">{formatValue(rmsCornerError * 1000, 1)} mm</strong>.
      </> : null}
    </p>;
  }
  if (event.type === "tool.started") return <p>Validated bounded arguments. The controller owns every physics step.</p>;
  if (event.type === "run.started") return <p>{payload.data_provenance === "synthetic_illustration" ? String(payload.reason) : "Preparing GPU, Newton world, assets, and public event stream."}</p>;
  return <p>{String(payload.reason ?? payload.follow_up_decision ?? payload.controller_result ?? payload.outcome ?? "Committed to the run event log.")}</p>;
}

function EvidenceCard({ event }: { event: AgentEvent }) {
  const inspect = useRunStore((state) => state.inspect);
  const expandable = event.type.startsWith("tool.") || event.type === "measurement.sampled";
  const lane = eventLane(event);
  return (
    <article className={`evidence-card evidence-card--${event.type.replace(".", "-")}`} tabIndex={0} onFocus={() => inspect(event.sequence)}>
      <header>
        <span className="evidence-icon" aria-hidden="true">{iconFor(event)}</span>
        <div>
          <strong>{eventTitle(event)}</strong>
          <span className="mono">#{event.sequence.toString().padStart(4, "0")} · {event.sim_time_s?.toFixed(2) ?? "—"} s</span>
        </div>
        <div className="evidence-badges">
          <Badge className={`lane-badge lane-badge--${lane.toLowerCase()}`}>{lane}</Badge>
          {event.stage && <Badge>{event.stage.replaceAll("_", " ")}</Badge>}
        </div>
      </header>
      <EventSummary event={event} />
      {expandable && (
        <CollapsiblePrimitive.Root>
          <CollapsiblePrimitive.Trigger className="evidence-details-trigger">
            Inspect structured payload <ChevronDown />
          </CollapsiblePrimitive.Trigger>
          <CollapsiblePrimitive.Content>
            <pre aria-label={`${eventTitle(event)} payload`}>{JSON.stringify(event.payload, null, 2)}</pre>
          </CollapsiblePrimitive.Content>
        </CollapsiblePrimitive.Root>
      )}
    </article>
  );
}

const visibleTypes = new Set<AgentEvent["type"]>([
  "run.started", "run.completed", "run.failed", "rationale.published", "tool.started", "tool.completed", "tool.failed",
  "measurement.summary", "belief.updated", "simulation.belief_rebuilt", "prediction.completed", "action.selected",
  "action.executed", "verification.completed", "learning.completed", "safety.triggered",
]);

export function EvidenceFeed({ run }: { run: RunState }) {
  const bufferedCount = useRunStore((state) => state.bufferedCount);
  const returnToLive = useRunStore((state) => state.returnToLive);
  const cards = run.events.filter((event) => visibleTypes.has(event.type));
  return (
    <section className="evidence-panel" aria-labelledby="evidence-title">
      <header className="region-header">
        <div><span className="eyebrow">Inspectability</span><h2 id="evidence-title">Agent evidence</h2></div>
        <Badge>{cards.length} events</Badge>
      </header>
      {bufferedCount > 0 && <Button className="return-live" variant="primary" onClick={returnToLive}>{bufferedCount} new events · Return to live</Button>}
      <div className="evidence-list" role="feed" aria-busy={run.status === "preparing"}>
        {cards.length === 0 ? (
          <div className="empty-state">
            <Bot />
            <h3>Evidence appears here</h3>
            <p>Run the discovery demo to inspect rationale, tool calls, measurements, and belief changes.</p>
          </div>
        ) : cards.map((event) => <EvidenceCard key={event.event_id} event={event} />)}
      </div>
    </section>
  );
}
