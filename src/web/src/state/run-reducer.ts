import type { ActionGraphSnapshot, AgentEvent, DetailedStage, VisualFramePayload } from "../generated/agent-events";
import { actionGraphCompiledPayloadSchema, actionNodeTransitionPayloadSchema, visualFramePayloadSchema } from "../generated/agent-events";

export type RunStatus = "idle" | "preparing" | "running" | "pause_pending" | "paused" | "completed" | "failed" | "cancelled" | "abandoned";
export type StageStatus = "pending" | "active" | "completed" | "failed" | "skipped";
export type ActionNodeState = "pending" | "entered" | "completed" | "failed" | "safety_trip" | "goal_miss";

export interface StageInstance {
  id: string;
  stage: DetailedStage;
  iteration: number;
  status: StageStatus;
  firstSequence: number;
  lastSequence?: number;
  durationS?: number;
  reason?: string;
}

export interface MeasurementPoint {
  sequence: number;
  simTimeS: number;
  measuredForceN?: number;
  desiredForceN?: number;
  objectPositionM?: number;
  objectVelocityMps?: number;
}

export interface BeliefEstimate {
  parameter: string;
  mean: number;
  std: number;
  interval95: [number, number];
  revision: number;
  unit: string;
}

export type WorldScene = VisualFramePayload["scene"];

export interface WorldFrameState {
  frame: VisualFramePayload;
  sequence: number;
}

export interface RunState {
  runId: string | null;
  taskId: string | null;
  status: RunStatus;
  lastSequence: number;
  lastSimTimeS: number;
  seenEventIds: ReadonlySet<string>;
  events: readonly AgentEvent[];
  stageInstances: readonly StageInstance[];
  activeStageInstanceId: string | null;
  worldFrames: Readonly<Record<WorldScene, WorldFrameState | null>>;
  activeScene: WorldScene | null;
  measurements: readonly MeasurementPoint[];
  predictionEvents: readonly AgentEvent[];
  selectedActionId: string | null;
  actionGraph: ActionGraphSnapshot | null;
  actionGraphId: string | null;
  actionGraphSequence: number | null;
  actionNodeStates: Readonly<Record<string, ActionNodeState>>;
  activeActionNodeId: string | null;
  lastActionNodeId: string | null;
  actionGraphContractError: string | null;
  causalExperimentName: string | null;
  causalEvidenceId: string | null;
  causalPatchLabel: string | null;
  causalDeploymentAction: string | null;
  beliefs: Readonly<Record<string, BeliefEstimate>>;
  gap: { expected: number; received: number } | null;
  fatalError: string | null;
  verification: { success: boolean; residualM?: number; toleranceM?: number } | null;
}

export const initialRunState: RunState = {
  runId: null,
  taskId: null,
  status: "idle",
  lastSequence: 0,
  lastSimTimeS: 0,
  seenEventIds: new Set(),
  events: [],
  stageInstances: [],
  activeStageInstanceId: null,
  worldFrames: { truth: null, belief: null },
  activeScene: null,
  measurements: [],
  predictionEvents: [],
  selectedActionId: null,
  actionGraph: null,
  actionGraphId: null,
  actionGraphSequence: null,
  actionNodeStates: {},
  activeActionNodeId: null,
  lastActionNodeId: null,
  actionGraphContractError: null,
  causalExperimentName: null,
  causalEvidenceId: null,
  causalPatchLabel: null,
  causalDeploymentAction: null,
  beliefs: {},
  gap: null,
  fatalError: null,
  verification: null,
};

function number(payload: Record<string, unknown>, key: string): number | undefined {
  const value = payload[key];
  return typeof value === "number" ? value : undefined;
}

function updateStage(state: RunState, event: AgentEvent): Pick<RunState, "stageInstances" | "activeStageInstanceId"> {
  if (!event.stage || !event.stage_instance_id) {
    return { stageInstances: state.stageInstances, activeStageInstanceId: state.activeStageInstanceId };
  }
  const instances = [...state.stageInstances];
  const index = instances.findIndex((item) => item.id === event.stage_instance_id);
  if (event.type === "stage.entered") {
    const next: StageInstance = {
      id: event.stage_instance_id,
      stage: event.stage,
      iteration: event.iteration_id,
      status: "active",
      firstSequence: event.sequence,
    };
    if (index >= 0) instances[index] = next;
    else instances.push(next);
    return { stageInstances: instances, activeStageInstanceId: next.id };
  }
  if (event.type === "stage.completed") {
    const outcome = String(event.payload.outcome ?? "completed") as StageStatus;
    const previous = index >= 0 ? instances[index] : undefined;
    const next: StageInstance = {
      id: event.stage_instance_id,
      stage: event.stage,
      iteration: event.iteration_id,
      status: ["failed", "skipped"].includes(outcome) ? outcome : "completed",
      firstSequence: previous?.firstSequence ?? event.sequence,
      lastSequence: event.sequence,
      durationS: number(event.payload, "duration_s"),
      reason: String(event.payload.failure_reason ?? event.payload.skipped_reason ?? "") || undefined,
    };
    if (index >= 0) instances[index] = next;
    else instances.push(next);
    return { stageInstances: instances, activeStageInstanceId: null };
  }
  return { stageInstances: state.stageInstances, activeStageInstanceId: state.activeStageInstanceId };
}

function statusFor(event: AgentEvent, current: RunStatus): RunStatus {
  const states: Partial<Record<AgentEvent["type"], RunStatus>> = {
    "run.started": "preparing",
    "run.paused": "paused",
    "run.resumed": "running",
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.abandoned": "abandoned",
    "stage.entered": "running",
  };
  return states[event.type] ?? current;
}

function activeSceneFor(event: AgentEvent, current: WorldScene | null): WorldScene | null {
  if (["run.completed", "run.failed", "run.cancelled", "run.abandoned", "run.paused", "stage.completed"].includes(event.type)) {
    return null;
  }
  if (event.type !== "stage.entered") return current;
  if (["act", "measure", "execute", "verify"].includes(event.stage ?? "")) return "truth";
  if (["update_simulation", "predict"].includes(event.stage ?? "")) return "belief";
  return null;
}

export function reduceRunEvent(state: RunState, event: AgentEvent): RunState {
  if (state.seenEventIds.has(event.event_id)) return state;
  if (state.runId && state.runId !== event.run_id) {
    return { ...state, fatalError: `Wrong run: expected ${state.runId}, received ${event.run_id}` };
  }
  const expected = state.lastSequence + 1;
  if (event.sequence !== expected) {
    if (event.sequence <= state.lastSequence) return state;
    return { ...state, gap: { expected, received: event.sequence } };
  }

  const seen = new Set(state.seenEventIds);
  seen.add(event.event_id);
  const base: RunState = {
    ...state,
    runId: event.run_id,
    status: statusFor(event, state.status),
    lastSequence: event.sequence,
    lastSimTimeS: event.sim_time_s ?? state.lastSimTimeS,
    seenEventIds: seen,
    events: [...state.events, event].slice(-500),
    activeScene: activeSceneFor(event, state.activeScene),
    gap: null,
    ...updateStage(state, event),
  };

  if (event.type === "run.started") {
    return {
      ...base,
      taskId: typeof event.payload.task_id === "string" ? event.payload.task_id : state.taskId,
    };
  }

  if (event.type === "simulation.visual_frame") {
    const frame = visualFramePayloadSchema.parse(event.payload);
    return {
      ...base,
      worldFrames: {
        ...state.worldFrames,
        [frame.scene]: { frame, sequence: event.sequence },
      },
    };
  }
  if (event.type === "action.graph_compiled") {
    const payload = actionGraphCompiledPayloadSchema.parse(event.payload);
    return {
      ...base,
      actionGraph: payload.graph,
      actionGraphId: payload.graph_id,
      actionGraphSequence: event.sequence,
      actionNodeStates: Object.fromEntries(payload.graph.nodes.map((node) => [node.node_id, "pending" as const])),
      activeActionNodeId: null,
      lastActionNodeId: null,
      actionGraphContractError: null,
    };
  }
  if (event.type === "action.node_transitioned") {
    const transition = actionNodeTransitionPayloadSchema.parse(event.payload);
    if (!state.actionGraph || transition.graph_id !== state.actionGraphId) {
      return { ...base, actionGraphContractError: "Node transition does not match the admitted action graph." };
    }
    if (!state.actionGraph.nodes.some((node) => node.node_id === transition.node_id)) {
      return { ...base, actionGraphContractError: `Unknown action graph node: ${transition.node_id}` };
    }
    return {
      ...base,
      actionNodeStates: { ...state.actionNodeStates, [transition.node_id]: transition.state },
      activeActionNodeId: transition.state === "entered" ? transition.node_id : state.activeActionNodeId === transition.node_id ? null : state.activeActionNodeId,
      lastActionNodeId: transition.node_id,
      actionGraphContractError: null,
    };
  }
  if (event.type.startsWith("tool.")) {
    return {
      ...base,
      causalExperimentName: typeof event.payload.tool_name === "string"
        ? event.payload.tool_name
        : state.causalExperimentName,
    };
  }
  if (event.type === "measurement.sampled") {
    const values = (event.payload.values ?? {}) as Record<string, unknown>;
    const point: MeasurementPoint = {
      sequence: event.sequence,
      simTimeS: event.sim_time_s ?? state.lastSimTimeS,
      measuredForceN: number(values, "measured_force_n"),
      desiredForceN: number(values, "desired_force_n"),
      objectPositionM: number(values, "object_position_m"),
      objectVelocityMps: number(values, "object_velocity_mps"),
    };
    return {
      ...base,
      measurements: [...state.measurements, point].slice(-2000),
      causalEvidenceId: typeof event.payload.evidence_id === "string"
        ? event.payload.evidence_id
        : state.causalEvidenceId,
    };
  }
  if (event.type === "measurement.summary") {
    return {
      ...base,
      causalEvidenceId: typeof event.payload.evidence_id === "string"
        ? event.payload.evidence_id
        : state.causalEvidenceId,
    };
  }
  if (event.type === "simulation.belief_rebuilt") {
    return {
      ...base,
      predictionEvents: [],
      selectedActionId: null,
      causalPatchLabel: `revision ${String(event.payload.belief_revision ?? "—")}`,
    };
  }
  if (event.type === "prediction.completed") {
    return { ...base, predictionEvents: [...state.predictionEvents, event].slice(-8) };
  }
  if (event.type === "action.selected") {
    return {
      ...base,
      selectedActionId: typeof event.payload.selected_action_id === "string"
        ? event.payload.selected_action_id
        : null,
    };
  }
  if (event.type === "action.executed") {
    return {
      ...base,
      causalDeploymentAction: typeof event.payload.action_id === "string"
        ? event.payload.action_id
        : state.causalDeploymentAction,
    };
  }
  if (event.type === "belief.updated") {
    const parameter = String(event.payload.parameter_name ?? "unknown");
    const posterior = (event.payload.posterior ?? {}) as Record<string, unknown>;
    const uncertainty = (posterior.uncertainty ?? {}) as Record<string, unknown>;
    const interval = uncertainty.interval95 ?? posterior.interval_95;
    const interval95: [number, number] = Array.isArray(interval) && interval.length === 2
      ? [Number(interval[0]), Number(interval[1])]
      : [Number(posterior.mean ?? 0), Number(posterior.mean ?? 0)];
    return {
      ...base,
      beliefs: {
        ...state.beliefs,
        [parameter]: {
          parameter,
          mean: Number(posterior.mean ?? 0),
          std: Number(uncertainty.standard_deviation ?? posterior.std ?? 0),
          interval95,
          revision: Number(event.payload.revision ?? 0),
          unit: String(posterior.units ?? event.payload.unit ?? ""),
        },
      },
      causalPatchLabel: `${parameter} · revision ${String(event.payload.revision ?? "—")}`,
    };
  }
  if (event.type === "verification.completed") {
    return {
      ...base,
      verification: {
        success: Boolean(event.payload.success),
        residualM: number(event.payload, "prediction_residual_m"),
        toleranceM: number(event.payload, "tolerance_m"),
      },
    };
  }
  return base;
}

export function reduceRunEvents(events: readonly AgentEvent[]): RunState {
  return events.reduce(reduceRunEvent, initialRunState);
}

export function seekRun(events: readonly AgentEvent[], sequence: number): RunState {
  return reduceRunEvents(events.filter((event) => event.sequence <= sequence));
}
