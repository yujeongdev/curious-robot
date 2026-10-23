import { eventTypes, parseAgentEvent, type AgentEvent } from "../generated/agent-events";

export interface CreateRunInput {
  task_id: string;
  seed: number;
  device: string;
  include_robot: boolean;
  recording: boolean;
  evaluator_mode: boolean;
  material_bounds: Record<string, unknown>;
}

export interface CreateRunResponse { run_id: string }
export interface TaskSummary {
  task_id: string;
  objective: string;
  object_label: string;
  requires_yaw: boolean;
  candidate_count: number;
  default_material_bounds?: Record<string, unknown>;
}

function mutationHeaders(): HeadersInit {
  const token = document.querySelector<HTMLMetaElement>('meta[name="curious-robot-control-token"]')?.content
    || sessionStorage.getItem("curious-robot-control-token")
    || "";
  return { "content-type": "application/json", "X-Curious-Robot-Control-Token": token };
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function createRun(input: CreateRunInput): Promise<CreateRunResponse> {
  return responseJson(await fetch("/api/runs", {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(input),
  }));
}

export async function loadTasks(): Promise<TaskSummary[]> {
  return responseJson(await fetch("/api/tasks"));
}

export async function commandRun(runId: string, command: "pause" | "resume" | "step" | "cancel") {
  return responseJson<Record<string, unknown>>(await fetch(`/api/runs/${encodeURIComponent(runId)}/${command}`, { method: "POST", headers: mutationHeaders(), body: "{}" }));
}

export function subscribeToRun(
  runId: string,
  afterSequence: number,
  onEvent: (event: AgentEvent) => void,
  onConnection: (state: "open" | "error") => void,
): () => void {
  const url = `/api/runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}`;
  const source = new EventSource(url);
  source.onopen = () => onConnection("open");
  source.onerror = () => onConnection("error");
  const handle = (message: MessageEvent<string>) => {
    try {
      onEvent(parseAgentEvent(JSON.parse(message.data)));
    } catch (error) {
      console.error("Rejected invalid agent event", error);
      onConnection("error");
      source.close();
    }
  };
  source.onmessage = handle;
  for (const eventType of eventTypes) source.addEventListener(eventType, handle as EventListener);
  return () => {
    for (const eventType of eventTypes) source.removeEventListener(eventType, handle as EventListener);
    source.close();
  };
}

export async function loadReplay(runId: string): Promise<AgentEvent[]> {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/events.ndjson`);
  if (!response.ok) throw new Error(`Replay unavailable: ${response.status}`);
  const text = await response.text();
  return text.split("\n").filter(Boolean).map((line) => parseAgentEvent(JSON.parse(line)));
}
