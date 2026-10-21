import { create } from "zustand";
import type { AgentEvent } from "../generated/agent-events";
import { initialRunState, reduceRunEvent, seekRun, type RunState, type WorldScene } from "./run-reducer";

interface RunStore {
  run: RunState;
  sourceEvents: readonly AgentEvent[];
  mode: "live" | "replay";
  followingLive: boolean;
  selectedSequence: number | null;
  bufferedCount: number;
  cameraModes: Readonly<Record<WorldScene, "guided" | "manual">>;
  ingest: (event: AgentEvent) => void;
  loadReplay: (events: readonly AgentEvent[]) => void;
  seek: (sequence: number) => void;
  inspect: (sequence: number) => void;
  returnToLive: () => void;
  setCameraMode: (scene: WorldScene, mode: "guided" | "manual") => void;
  reset: () => void;
}

export const useRunStore = create<RunStore>((set, get) => ({
  run: initialRunState,
  sourceEvents: [],
  mode: "live",
  followingLive: true,
  selectedSequence: null,
  bufferedCount: 0,
  cameraModes: { truth: "guided", belief: "guided" },
  ingest: (event) => {
    const state = get();
    const sourceEvents = [...state.sourceEvents, event];
    if (!state.followingLive) {
      set({ sourceEvents, bufferedCount: state.bufferedCount + 1 });
      return;
    }
    set({ sourceEvents, run: reduceRunEvent(state.run, event), selectedSequence: event.sequence });
  },
  loadReplay: (events) => set({
    run: initialRunState,
    sourceEvents: events,
    mode: "replay",
    followingLive: false,
    selectedSequence: 0,
    bufferedCount: 0,
  }),
  seek: (sequence) => {
    const state = get();
    set({ run: seekRun(state.sourceEvents, sequence), selectedSequence: sequence });
  },
  inspect: (sequence) => {
    const state = get();
    set({
      followingLive: false,
      selectedSequence: sequence,
      run: seekRun(state.sourceEvents, sequence),
      bufferedCount: state.mode === "live" ? Math.max(0, state.sourceEvents.length - sequence) : 0,
    });
  },
  returnToLive: () => {
    const state = get();
    set({
      followingLive: true,
      mode: "live",
      run: seekRun(state.sourceEvents, Number.POSITIVE_INFINITY),
      selectedSequence: state.sourceEvents.at(-1)?.sequence ?? null,
      bufferedCount: 0,
    });
  },
  setCameraMode: (scene, cameraMode) => set((state) => ({
    cameraModes: { ...state.cameraModes, [scene]: cameraMode },
  })),
  reset: () => set({
    run: initialRunState,
    sourceEvents: [],
    mode: "live",
    followingLive: true,
    selectedSequence: null,
    bufferedCount: 0,
    cameraModes: { truth: "guided", belief: "guided" },
  }),
}));
