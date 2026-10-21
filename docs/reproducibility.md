# Replay and reproducibility

Curious Robot deliberately provides two different replay products.

## Visual state replay

`scene_replay.bin` is written by Newton `ViewerFile`. It stores model and state
snapshots for visual inspection. Use:

```bash
uv run curious-robot-replay --visual artifacts/demo/scene_replay.bin
```

The printed official Newton command opens a UI with pause, seek, and single
frame stepping. This is playback, not a physics determinism claim.

## Deterministic experiment re-execution

`reexecution.json` records the seed, package versions, device, solver and
timestep settings, scene parameters, hidden evaluation parameters, initial
cube state, exact final-action force sequence, reference state trajectory,
sensor settings, agent tool calls, and belief history. Run:

```bash
uv run curious-robot-replay artifacts/demo/reexecution.json --verify
```

The verifier rebuilds a fresh Newton truth world, reapplies every recorded
low-level force command, and writes `reexecution_report.json`. Warp's
`RUN_TO_RUN` mode reduces nondeterminism on the same GPU, but bitwise identity
across GPU architectures, drivers, or package versions is not promised. A
fresh solver also cannot restore every private contact warm-start buffer from
public Newton state. The explicit position tolerance is therefore part of the
manifest and every report, and a failed report is retained as evidence rather
than hidden. The reduced cube-only verifier uses 5 mm; the full articulated
Franka/contact replay uses 15 mm.
