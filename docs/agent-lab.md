# Agent Lab

Agent Lab is the local visual workspace for Curious Robot. It renders the
authoritative Newton event stream alongside the evidence that caused each
experiment, belief update, and manipulation decision.

## Design contract

- Newton is the only physics authority.
- React Three Fiber renders recorded body transforms; it does not simulate.
- The interface shows structured rationale and evidence IDs, never private
  chain-of-thought.
- Live mode and replay use the same typed events and pure reducer.
- Hidden truth is unavailable before terminal evaluator reveal.
- Safety state and the current discovery phase remain visible at all times.

## Workspace

The desktop layout reserves the primary region for two synchronized Newton
viewports and the secondary region for the evidence feed:

- **Reality Stand-in · Hidden Physics** shows only robot experiments and fresh deployment. It
  owns the hidden episode dynamics but exposes only public observations.
- **Learned Newton Twin · Estimated Physics** shows only posterior calibration and counterfactual
  action rollouts. Its parameters come from evidence-backed belief updates.

Both panes retain their last committed frame on one causal event cursor. Their
local simulation times are independent: while one world advances, the other is
explicitly marked `HELD`. The UI never copies a Reality transform into the Twin
to make them appear synchronized.

For `push-t-bar-v1`, the goal marker is the public T-bar geometry projected
onto the tabletop at the target pose. The physical T-bar must overlap that
same-shape silhouette within the task's planar-position and yaw tolerances.
The marker comes from the recorded target manifest; R3F does not infer it.

The six-phase rail shows the scientific loop. After policy selection, a separate
ActionGraph inspector shows the deterministic execution contract and its recorded
node progress. Reality experiments, observation boundaries, posterior updates,
Twin validation, and fresh Reality deployment remain auditable in the evidence
history rather than occupying a second permanent progress rail.

Evidence cards are independently labeled `REALITY`, `TWIN`, `INFERENCE`, or
`SYSTEM`. A synchronized telemetry dock keeps measured force, posterior
parameters, candidate worlds, and predicted-versus-observed outcomes separate.
Mobile views retain both physics states and use a 42svh world selector to show
one at a time.

The stage rail groups the execution into six phases:

1. Observe
2. Discover
3. Experiment
4. Calibrate
5. Decide
6. Execute

Repeated experiments retain their iteration identity rather than overwriting
earlier evidence.

## ActionGraph inspection

After a controller publishes an admitted ActionGraph, the execution inspector
shows the recorded current node, its neighboring success-path nodes, the graph
deadline, and the shared safe-stop exit. The complete topology and contract IDs
remain behind `Inspect full graph` so the Newton view stays visually primary.

The browser never derives a graph from an action name and never advances a node.
It reduces only `action.graph_compiled` and `action.node_transitioned` events from
the append-only run log. A backend that has not emitted those records is shown as
`NOT ADMITTED`; the UI does not fabricate an execution trace. Live and replay
therefore render the same graph state at the same event cursor.

## Event and replay rules

Every event carries a run ID, event ID, sequence, wall time, discovery stage,
and optional simulation/frame identity. The event store is append-only.

At replay cursor `S`, each pane renders its own newest committed state at or
before `S`. Visual playback and deterministic physics re-execution remain
separate: the former displays committed states, while the latter runs fresh
worlds and compares numerical trajectories.

## Running locally

```bash
uv sync --extra dev --extra web
corepack pnpm --dir src/web install --frozen-lockfile
corepack pnpm --dir src/web build
uv run curious-robot web --open
```

The server is local-only by default. It has no authentication layer and should
not be exposed to an untrusted network.

## Visual system

The UI follows [DESIGN.md](../DESIGN.md): graphite workspace, one cyan signal
color, IBM Plex Sans KR and IBM Plex Mono, hairline regions, and deterministic
motion. Generic card-dashboard styling and decorative animation are excluded.

## Media capture

The capture pipeline replays a completed event log on a fixed clock, waits for
both world-specific reducer and WebGL frame barriers, rejects a blank pane,
encodes H.264, derives a GIF preview, and records provenance. See
[media/README.md](media/README.md).
