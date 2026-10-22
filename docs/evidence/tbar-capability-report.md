# T-bar Reality → Twin fleet → Reality acceptance

> Adjacent documents: [Validation](../validation.md) · [Architecture](../architecture.md)

2026-10-22. Scope: one evaluator-seeded NVIDIA Newton acceptance run for
`push-t-bar-v1@1.1.0`.
Basis: the documented `lab-friction` command with seed `7`; generated artifacts
remain local and excluded from Git.

## Result

- Terminal success: `true`
- Planar target error: `0.007912 m` against a `0.030 m` tolerance
- Wrapped yaw error: `0.081075 rad` (`4.645°`) against a `9°` MVP tolerance
- Selected candidate: `push_tbar_fast`
- Selected contact plan: `0.080 m/s`, `0.012 m` push height,
  `+0.0125 m` lateral contact offset
- Peak predicted candidate force: `3.271 N`; observed deployment peak: `6.576 N`
- Dynamic-friction posterior: `0.482877 ± 0.195249`
- Physical experiment protocol `1.2.0`:
  `sha256:1091406e1dc4d685246b52fbbf6c5046d973d52a9e3fe9d6edf82161b66c76d1`

The candidate was selected from three independently allocated Newton Twin
worlds. All three used the same evidence-backed posterior and public geometry
hash, but different bounded contact plans.

## Authority and identity evidence

- Reality experiment world: `06d6e53e-0de0-430a-a0bc-0a8160b94d70`
- Twin candidate worlds:
  - `706b104f-8608-4938-a30a-4f55e2307462`
  - `807713cd-2040-45c2-bfe3-f3a8bf19f62f`
  - `abec66bb-f37d-4362-9cf7-f100e65c55e5` (selected)
- Fresh Reality deployment world: `f3bb0ccb-b091-40ed-92e1-4ac83991e849`
- Public geometry: one T-bar rigid body, two non-overlapping box parts
- Floor goal: the same two-part T-bar geometry projected at the target pose
- Geometry hash: `sha256:a3e9d55450c4813a039f75718cea15322400df1b6d86d3f857ee9345cf953682`

The experiment, every candidate, and deployment identifiers are distinct.
The evaluator passed `position_within_tolerance`, `yaw_within_tolerance`,
`controller_completed`, and `robot_safe`.

The retained evaluator seed sampled initial yaw `-7.32°`. Success was checked
again after the configured terminal settling interval; the controller cannot
retain an earlier success if the T-bar later drifts outside the pose goal.

The 9° tolerance is the explicit first-MVP contract for friction-only,
observed-yaw T-bar pushing. The research acceptance target remains ≤5° across at
least 8 of 10 evaluator seeds after planar COM/yaw-inertia identification is
added. The 2.35° pre-COM result was discarded and is not used as evidence.

The separately retained integrated Agent Lab run
`edc7af93-d574-499f-957b-fb45def44922` also completed successfully after two
agent-selected probes and three Twin rollouts. Its physics re-execution passed
the 15 mm position comparison but failed the stricter yaw determinism check
(0.101 rad observed versus 0.010 rad tolerance). This nondeterminism is
reported rather than reclassified as a deterministic replay pass.

That integrated run sampled initial yaw `-7.32°`. The selected policy used two
observed-yaw corrections on `crossbar_right_y`, then translated from
`stem_rear_x`. Fresh Reality finished with `8.83 mm` position error and
`8.49°` wrapped-yaw error; the Twin–Reality position residual was `2.98 mm`.
The current validated random-yaw envelope is intentionally bounded to
`[-8°, -5°]`; symmetric arbitrary-yaw placement remains future work.

## Boundary

The report contains public evidence, posterior values, content identities, and
the terminal evaluator verdict. Hidden scenario mass, friction, restitution,
and private scenario seed are intentionally absent. This run used the public
uniform-density COM assumption; COM and yaw inertia were not learned from the
friction-only evidence.

T-bar capability distillation and physical-hardware execution remain
fail-closed; this run validates the simulation-only laboratory path.
