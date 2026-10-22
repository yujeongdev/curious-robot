# Validation status

This page distinguishes executed evidence from planned capability.

## Executed

- Franka articulation and tool-tip collision are simulated by Newton with
  `SolverMuJoCo` / MuJoCo Warp.
- Static and dynamic probes move the robot through bounded Cartesian plans and
  accept only native robot–object contact samples.
- Reality and Twin run in separate spawned processes with distinct world,
  model, state, and configuration identities.
- Posterior parameters reach the Twin through an immutable `WorldSpec` patch.
- A Twin-validated push policy is deployed into a newly allocated Reality
  world and terminally evaluated.
- The T-bar task builds one rigid body from two non-overlapping public box
  parts, records contact against both constituent shapes, evaluates three
  independent Twin candidate worlds, and deploys the selected typed contact
  plan into a fresh Reality world.
- The executed T-bar acceptance run passed both its 30 mm planar-position and
  8° wrapped-yaw predicates for the first friction-only MVP. Its experiment,
  candidate, and deployment worlds had distinct identities.
- Public artifacts exclude sampled mass, friction, restitution, and private
  scenario seed values.
- Visual replay and numerical re-execution are independently tested. For the
  retained integrated T-bar run, re-execution passed the 15 mm position bound
  (`10.62 mm` maximum error) but failed the 0.010 rad yaw determinism bound
  (`0.161 rad` maximum error); the generated report remains `passed: false`.

The latest cube run is summarized in the
[friction capability report](evidence/friction-capability-report.md). Executed
T-bar evidence is in the
[T-bar acceptance report](evidence/tbar-capability-report.md). Additional
reference evidence is under [evidence/](evidence/). Demo media is generated
from a committed event recording and published with a versioned release.

## Current scientific limits

- Mass is not identified when bilateral grasp/lift is unavailable.
- Static and dynamic friction are effective interaction parameters, not
  intrinsic material coefficients.
- The public contact sensor exposes linear force; a validated wrist moment is
  not available.
- Broad or structured residuals remain visible as bounded or unidentifiable
  posteriors.
- A task can succeed while the prediction residual remains large; task success
  and model agreement are reported separately.
- Hardware interfaces and preflight checks are implemented, but no physical
  robot trial is claimed.
- The first T-bar slice fixes COM to the public uniform-density geometry
  centroid; it is an assumption, not an inferred episode parameter. Yaw inertia
  is likewise not independently identified by the current friction probes.
- Deterministic capability distillation and hardware deployment reject T-bar
  policies until geometry-aware applicability has its own acceptance evidence.

## Release gates

```bash
uv run ruff check src tests
uv run pytest -m "not gpu"
uv run pytest -m gpu                 # NVIDIA workstation

corepack pnpm --dir src/web lint
corepack pnpm --dir src/web typecheck
corepack pnpm --dir src/web test
corepack pnpm --dir src/web build
corepack pnpm --dir src/web test:e2e
```

The CI workflow runs the portable Python and frontend gates. GPU physics,
re-execution, and media capture run on a supported local NVIDIA workstation.
