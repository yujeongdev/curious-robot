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

The verifier rebuilds a fresh Newton truth world, reapplies the recorded force
trace, and writes `reexecution_report.json`. `RUN_TO_RUN` reduces nondeterminism
on the same GPU, but bitwise identity across GPU architectures, drivers, or
package versions is not promised. The explicit position tolerance is therefore
part of the manifest and every report.
