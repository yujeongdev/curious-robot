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
  plan into a fresh Reality world. Its floor target uses the exact same public
  compound geometry, so success means aligning the T-bar with a T-shaped
  silhouette rather than entering a circular region.
- The executed T-bar acceptance run passed both its 30 mm planar-position and
  9° wrapped-yaw predicates for the first friction-only MVP. The retained seed
  finished at `7.91 mm` center error and `4.65°` yaw error. Exact public-geometry
  registration measured `13.46 mm` maximum and `9.80 mm` RMS corner error.
  Its experiment, candidate, and deployment worlds had distinct identities.
- A randomized-start Agent Lab run sampled initial yaw `-7.32°`, used
  `crossbar_right_y → crossbar_right_y → stem_rear_x`, and passed in fresh
  Reality with `8.83 mm` position error, `8.49°` yaw error, and `2.98 mm`
  Twin–Reality position residual. The validated random-yaw envelope is
  currently `[-8°, -5°]`; positive and arbitrary yaw remain unsupported.
- Public artifacts exclude sampled mass, friction, restitution, and private
  scenario seed values.
- Visual replay and numerical re-execution are independently tested. For the
  retained integrated T-bar run, re-execution passed the 15 mm position bound
  (`11.24 mm` maximum error) but failed the 0.010 rad yaw determinism bound
  (`0.101 rad` maximum error); the generated report remains `passed: false`.

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
- Hardware plans now embed a typed ActionGraph and require graph-ID attestation
  from preflight and transport results. No concrete pylibfranka/ROS 2 transport
  or native 1 kHz executor is implemented, so hardware execution remains unavailable.
- Wrist RGB-D contracts validate intrinsics, hand-eye calibration, corrected
  camera/robot timestamp agreement, image/depth encoding and artifacts, and
  positive-semidefinite pose covariance. A simulated frame
  producer and real camera adapter remain planned rather than claimed.
- The first T-bar slice fixes COM to the public uniform-density geometry
  centroid; it is an assumption, not an inferred episode parameter. Yaw inertia
  is likewise not independently identified by the current friction probes.
- Deterministic capability distillation and hardware deployment reject T-bar
  policies until geometry-aware applicability has its own acceptance evidence.
- A single retained seed is not a general precision claim. The proposed
  held-out gate is at least 8 of 10 episodes with position ≤10 mm, yaw ≤5°,
  and maximum registered-corner error ≤12 mm, while every episode must remain
  inside the existing 30 mm/9° task contract.

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
