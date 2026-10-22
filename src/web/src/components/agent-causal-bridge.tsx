import type { AgentEvent } from "../generated/agent-events";
import type { RunState } from "../state/run-reducer";

type BridgeState = "complete" | "active" | "waiting";

export interface TwinCandidate {
  id: string;
  scenarioId: string;
  worldId: string;
  beliefRevision: string;
  status: "SELECTED" | "FEASIBLE" | "REJECTED" | "RECORDED";
  result: string;
}

function lastEvent(run: RunState, predicate: (event: AgentEvent) => boolean): AgentEvent | undefined {
  for (let index = run.events.length - 1; index >= 0; index -= 1) {
    const event = run.events[index];
    if (event && predicate(event)) return event;
  }
  return undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function probability(value: unknown): string | undefined {
  return typeof value === "number" ? `${Math.round(value * 100)}% success` : undefined;
}

export function deriveTwinCandidates(run: RunState): TwinCandidate[] {
  const selected = run.selectedActionId;
  const candidates = new Map<string, TwinCandidate>();

  for (const event of run.predictionEvents) {
    if (event.type === "prediction.completed") {
      const id = text(event.payload.action_id) ?? text(event.payload.scenario_id);
      if (!id) continue;
      const feasible = event.payload.feasible !== false;
      candidates.set(id, {
        id,
        scenarioId: text(event.payload.scenario_id) ?? "scenario not recorded",
        worldId: text(event.payload.world_instance_id) ?? "world not recorded",
        beliefRevision: `rev ${String(event.payload.belief_revision ?? "—")}`,
        status: id === selected ? "SELECTED" : feasible ? "FEASIBLE" : "REJECTED",
        result: probability(event.payload.success_probability)
          ?? (typeof event.payload.target_error_m === "number" ? `${event.payload.target_error_m.toFixed(3)} m error` : feasible ? "feasible" : "infeasible"),
      });
    }
  }
  const retainedTwin = run.worldFrames.belief?.frame;
  const scenarioId = text(retainedTwin?.scenario_id);
  const actionId = text(retainedTwin?.action_id) ?? scenarioId;
  if (retainedTwin && actionId && scenarioId && !candidates.has(actionId)) {
    candidates.set(actionId, {
      id: actionId,
      scenarioId,
      worldId: retainedTwin.world_instance_id,
      beliefRevision: `rev ${String(retainedTwin.belief_revision ?? "—")}`,
      status: actionId === selected ? "SELECTED" : "RECORDED",
      result: "Newton frame recorded",
    });
  }
  const ordered = [...candidates.values()];
  const selectedCandidate = ordered.find((candidate) => candidate.status === "SELECTED");
  return selectedCandidate
    ? [selectedCandidate, ...ordered.filter((candidate) => candidate !== selectedCandidate)].slice(0, 3)
    : ordered.slice(0, 3);
}

function bridgeState(run: RunState, complete: boolean, activeStages: string[]): BridgeState {
  const active = run.stageInstances.find((stage) => stage.id === run.activeStageInstanceId)?.stage;
  if (active && activeStages.includes(active)) return "active";
  return complete ? "complete" : "waiting";
}

function BridgeStep({ index, label, value, state }: { index: number; label: string; value: string; state: BridgeState }) {
  return (
    <li data-state={state} aria-current={state === "active" ? "step" : undefined}>
      <span className="bridge-index">{String(index).padStart(2, "0")}</span>
      <span><strong>{label}</strong><small>{value}</small></span>
      <b>{state === "active" ? "CURRENT" : state === "complete" ? "COMMITTED" : "WAITING"}</b>
    </li>
  );
}

export function AgentCausalBridge({ run }: { run: RunState }) {
  const experiment = lastEvent(run, (event) => event.type.startsWith("tool.") && event.stage === "measure")
    ?? lastEvent(run, (event) => event.type === "rationale.published" && event.stage === "design_experiment");
  const observation = lastEvent(run, (event) => event.type === "measurement.summary" || event.type === "measurement.sampled");
  const patch = lastEvent(run, (event) => event.type === "simulation.belief_rebuilt" || (event.type === "belief.updated" && event.stage === "system_identification"));
  const predictions = run.predictionEvents;
  const deployment = lastEvent(run, (event) => event.type === "action.executed");

  const experimentName = run.causalExperimentName ?? text(experiment?.payload.tool_name) ?? text(experiment?.payload.selected_option_id) ?? "agent has not selected a probe";
  const evidenceId = run.causalEvidenceId ?? text(observation?.payload.evidence_id) ?? "observations only · hidden truth blocked";
  const revision = patch?.type === "simulation.belief_rebuilt"
    ? `revision ${String(patch.payload.belief_revision ?? "—")}`
    : patch ? `${text(patch.payload.parameter_name) ?? "parameter"} · revision ${String(patch.payload.revision ?? "—")}` : run.causalPatchLabel ?? "no evidence-backed patch yet";
  const deployedAction = run.causalDeploymentAction ?? text(deployment?.payload.action_id);
  const deploymentName = deployedAction
    ?? (run.selectedActionId ? `${run.selectedActionId} selected · awaiting Reality` : "not deployed");

  return (
    <section className="agent-causal-bridge" aria-labelledby="causal-bridge-title">
      <header><span className="eyebrow">Agent causal bridge</span><h2 id="causal-bridge-title">Observe, estimate, rebuild, validate, deploy</h2></header>
      <ol>
        <BridgeStep index={1} label="Reality experiment" value={experimentName} state={bridgeState(run, Boolean(run.causalExperimentName ?? experiment), ["design_experiment", "act"])} />
        <BridgeStep index={2} label="Observation boundary" value={evidenceId} state={bridgeState(run, Boolean(run.causalEvidenceId ?? observation), ["measure"])} />
        <BridgeStep index={3} label="Posterior → Twin patch" value={revision} state={bridgeState(run, Boolean(run.causalPatchLabel ?? patch), ["system_identification", "update_simulation"])} />
        <BridgeStep index={4} label="Candidate validation" value={predictions.length ? `${predictions.length} recorded Twin rollout${predictions.length === 1 ? "" : "s"}` : "no candidate result recorded"} state={bridgeState(run, predictions.length > 0, ["predict", "select_action"])} />
        <BridgeStep index={5} label="Fresh Reality deploy" value={deploymentName} state={bridgeState(run, Boolean(deployedAction), ["execute", "verify", "learn"])} />
      </ol>
    </section>
  );
}

export function TwinCandidateFleet({ run }: { run: RunState }) {
  const candidates = deriveTwinCandidates(run);
  const slots = Array.from({ length: 3 }, (_, index) => candidates[index] ?? null);
  return (
    <section className="twin-candidate-fleet" aria-labelledby="twin-fleet-title">
      <header>
        <span className="eyebrow">Independent experiment simulations</span>
        <h2 id="twin-fleet-title">Twin candidate fleet</h2>
        <small>Recorded events only · up to 3 visible</small>
      </header>
      <ol>
        {slots.map((candidate, index) => (
          <li key={candidate?.id ?? `empty-${index}`} data-status={candidate?.status.toLowerCase() ?? "waiting"}>
            <span className="mono">SIM {String(index + 1).padStart(2, "0")}</span>
            {candidate ? (
              <>
                <strong>{candidate.id.replaceAll("_", " ")}</strong>
                <small>{candidate.worldId === "world not recorded" ? candidate.worldId : candidate.worldId.slice(0, 8)} · {candidate.beliefRevision} · {candidate.result}</small>
                <b>{candidate.status}</b>
              </>
            ) : (
              <>
                <strong>No recorded candidate</strong>
                <small>The agent may launch another independent Newton world.</small>
                <b>WAITING</b>
              </>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
