# Curious Robot

**Discover Physics. Simulate. Act. Learn.**

Curious Robot is an open-source, simulation-only research vertical slice for
active physical system identification and autonomous manipulation. A Franka
Panda agent observes an unknown cube, chooses bounded experiments, estimates
the parameters relevant to its task, calibrates a separate belief simulation,
compares push and pick-and-place candidates, executes the selected action in a
hidden truth simulation, and explains the result.

The truth and belief environments are independent NVIDIA Newton simulations.
Ground-truth mass and friction are owned by the evaluation harness and cannot
be accessed through the robot's observation or planning interfaces.

## Quick start

Requirements: Linux, Python 3.12 or 3.13, and an NVIDIA R580+ driver for the
default Warp build. CPU execution is also supported for tests.

```bash
uv sync --extra dev
uv run curious-robot run --seed 7 --device cuda:0 --output artifacts/demo
uv run curious-robot-replay artifacts/demo/reexecution.json --verify
uv run pytest
```

Use `--device cpu` for a portable run. The run creates structured decisions,
measurements, belief history, replay state, parameter and trajectory plots,
and a final result summary under the output directory.

## Honest scope

This repository demonstrates simulated-real → system identification → belief
simulation → action planning → simulated-real. It does **not** claim a hardware
experiment. MuJoCo Warp contact is treated as non-differentiable here; physics
calibration therefore uses bounded SciPy optimization. The wrist wrench is a
documented virtual sensor derived from the bounded actuator/contact force path,
not a readout of hidden object parameters.

See `docs/architecture.md`, `docs/capabilities.json`, and
`docs/reproducibility.md` for boundaries and evidence.
