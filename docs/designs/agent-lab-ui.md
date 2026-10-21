# Agent Lab dual-world interaction contract

> Adjacent documents: [Agent Lab](../agent-lab.md) · [Architecture](../architecture.md) · [Design system](../../DESIGN.md)

2026-10-22. Scope: implementation rules for separating Simulated Reality and the learned Newton Twin in Agent Lab.
Basis: event contract v1 and the current dual-world replay implementation.

## Physics authority

React Three Fiber is a presentation mirror of committed Newton transforms. It
must never integrate motion, infer contact, mutate a posterior, or fabricate a
frame for either world.

The left pane is **Simulated Reality**. It displays bounded robot experiments,
measured outcomes, and fresh policy deployment. Hidden truth parameters are
not displayed before evaluator reveal.

The right pane is the **Learned Newton Twin**. It displays a world constructed
from public geometry, numerical configuration, and evidence-backed posterior
parameters. Calibration and candidate-action prediction belong here.

## Synchronization

The panes share one causal event cursor, not one simulation clock. At cursor
`S`, each pane shows its most recent committed frame at or before `S`. The pane
that is not advancing remains visible and is labeled `HELD`; an unavailable
world is labeled `WAITING`.

Every pane exposes its world instance, scenario, local simulation time, frame,
global event sequence, and—where applicable—belief revision. Equal frame
numbers or local times across panes do not imply paired physical states.

## Evidence lanes

- `REALITY`: measurements, robot/tool actions, safety events, and verification
- `TWIN`: world rebuilds and candidate predictions
- `INFERENCE`: belief updates, experiment/action rationale, selection, learning
- `SYSTEM`: lifecycle and orchestration events without a physical-world owner

The labels describe provenance, not visual grouping. Chronological order remains
the append-only event order.

## Interaction rules

- Each world owns an independent guided/manual camera mode.
- Camera changes never advance physics or alter scientific state.
- Replay seek restores both panes from the same causal cursor.
- Missing frames, predictions, and measurements are explicit; they are never
  replaced with zero values or copied from the other world.
- Task success and prediction agreement remain separate verdicts.
- The six-phase discovery rail stays visible; the four-step world-flow rail
  indicates where physical work is currently occurring.

## Capture acceptance

Published capture must use a completed authoritative event log, wait for the
expected frame identity in each available world, and reject a blank WebGL pane.
The Franka visual-clearance gate remains independent of this display check.
The canonical release capture is 1920×1080 H.264 with a derived opaque GIF.
