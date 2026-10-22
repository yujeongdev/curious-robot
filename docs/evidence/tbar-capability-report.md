# T-bar Reality → Twin fleet → Reality acceptance

> Adjacent documents: [Validation](../validation.md) · [Architecture](../architecture.md)

2026-10-22. Scope: one evaluator-seeded NVIDIA Newton acceptance run for `push-t-bar-v1`.
Basis: `artifacts/tbar-lab-final` generated locally and excluded from Git.

## Result

- Terminal success: `true`
- Planar target error: `0.014483 m` against a `0.030 m` tolerance
- Wrapped yaw error: `0.133697 rad` (`7.660°`) against a `9°` MVP tolerance
- Selected candidate: `push_tbar_fast`
- Selected contact plan: `0.080 m/s`, `0.012 m` push height,
  `+0.0125 m` lateral contact offset
- Peak predicted candidate force: `2.903 N`; observed deployment peak: `5.481 N`
- Dynamic-friction posterior: `0.486538 ± 0.196771`

The candidate was selected from three independently allocated Newton Twin
worlds. All three used the same evidence-backed posterior and public geometry
hash, but different bounded contact plans.

## Authority and identity evidence

- Reality experiment world: `2cabd430-5cb3-48ec-bb65-5c434d748e57`
- Twin candidate worlds:
  - `93d9d33f-8b4d-4a48-ae20-4512ab4d017b` (controller-rejected)
  - `7f8b68f3-d9bf-49d8-9378-04be8a7d0556`
  - `0d7e8192-556c-4e28-b9ef-208909225ae9` (selected)
- Fresh Reality deployment world: `a23bc61d-b059-4403-a8fb-d10828a7e223`
- Public geometry: one T-bar rigid body, two non-overlapping box parts
- Geometry hash: `sha256:a3e9d55450c4813a039f75718cea15322400df1b6d86d3f857ee9345cf953682`

The experiment, every candidate, and deployment identifiers are distinct.
The evaluator passed `position_within_tolerance`, `yaw_within_tolerance`,
`controller_completed`, and `robot_safe`.

The 9° tolerance is the explicit first-MVP contract for friction-only,
open-loop T-bar pushing. The research acceptance target remains ≤5° across at
least 8 of 10 evaluator seeds after planar COM/yaw-inertia identification is
added. The 2.35° pre-COM result was discarded and is not used as evidence.

The separately retained integrated Agent Lab run
`d8eafbb0-0d34-45cc-806e-e0bb7b00c93d` also completed successfully after two
agent-selected probes and three Twin rollouts. Its physics re-execution passed
the 15 mm position comparison but failed the stricter yaw determinism check
(0.161 rad observed versus 0.010 rad tolerance). This nondeterminism is
reported rather than reclassified as a deterministic replay pass.

## Boundary

The report contains public evidence, posterior values, content identities, and
the terminal evaluator verdict. Hidden scenario mass, friction, restitution,
and private scenario seed are intentionally absent. This run used the public
uniform-density COM assumption; COM and yaw inertia were not learned from the
friction-only evidence.

T-bar capability distillation and physical-hardware execution remain
fail-closed; this run validates the simulation-only laboratory path.
