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

- **Simulated Reality** shows only robot experiments and fresh deployment. It
  owns the hidden episode dynamics but exposes only public observations.
- **Learned Newton Twin** shows only posterior calibration and counterfactual
  action rollouts. Its parameters come from evidence-backed belief updates.

Both panes retain their last committed frame on one causal event cursor. Their
local simulation times are independent: while one world advances, the other is
explicitly marked `HELD`. The UI never copies a Reality transform into the Twin
to make them appear synchronized.

A four-step flow rail makes the world transition explicit:

1. Reality experiment
2. Posterior update and Twin rebuild
3. Twin validation
4. Fresh Reality deployment

Evidence cards are independently labeled `REALITY`, `TWIN`, `INFERENCE`, or
`SYSTEM`. A synchronized telemetry dock keeps measured force, posterior
parameters, and predicted-versus-observed outcomes in separate regions. Mobile
views retain both states and use a 42svh world selector to show one at a time.

The stage rail groups the execution into six phases:

1. Observe
2. Discover
3. Experiment
4. Calibrate
5. Decide
6. Execute

Repeated experiments retain their iteration identity rather than overwriting
earlier evidence.

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
