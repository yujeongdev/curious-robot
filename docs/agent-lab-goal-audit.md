# Agent Lab goal audit

This audit maps the Curious Robot MVP goal to executable evidence. The
reference portfolio run is `46f34e7b-c0b0-499f-b2c7-e02e83b6d0be` (seed 7,
Franka enabled, hidden wood-family surface). It completed successfully on an
RTX 4080 SUPER with Newton 1.7.0, Warp 1.18.0, and MuJoCo 3.15.0.

A later boundary-validation run, `physical-boundary-v9`, uses the contact-valid
sample schema, private evaluator scenario seed, exact protocol/config lineage,
and a non-trivial final push. It also completed successfully. Its large 130.9 mm
belief prediction residual is retained as model mismatch and widens the dynamic
friction uncertainty instead of being hidden.

| Goal | Implementation | Verification / evidence |
| --- | --- | --- |
| Hidden simulated reality | `TruthEnvironment`; evaluator-only `PhysicalParameters` and `SurfaceProfile` | truth-boundary tests; evaluator artifacts are terminal-gated |
| Independent belief simulation | `BeliefEnvironment.from_belief()` rebuilds a new Franka Newton model from posterior values | two `simulation.belief_rebuilt` events with distinct world IDs and `belief_simulation_config.json` |
| Active experiment choice | deterministic EIG − cost − risk planner with safety filtering | rationale events and planning tests |
| Robot physical experiment | Franka-mounted pusher, native MuJoCo contact, camera pose, no cube-force action | contact-valid subsets within 898 raw breakaway-ramp and 2,652 raw slide samples |
| Physical constraints | contact-gated interval-censored breakaway and bounded effective sliding fit | accepted/rejected contact counts, broad posterior bounds, and mismatch notes remain visible |
| Push versus pick decision | physical push rolls out in the belief world; unvalidated bilateral grasp is rejected | reference run selected `push_straight_low` after two prediction events |
| Execute and verify | bounded controller executes only after selection | final target success; prediction residual recorded in `result.json` |
| Live evidence UI | FastAPI/SSE plus React reducer, R3F presentation mirror, stage rail, evidence and telemetry | frontend unit/E2E tests and actual-run screenshot/video |
| Visual replay | Newton `ViewerFile` recording and Agent Lab event replay | `scene_replay.bin`; replay slider uses the same reducer as live mode |
| Physics re-execution | recorded joint-target sequence applied to a fresh world | `reexecution.json` and explicit numerical report; failures remain visible |
| Config versus learned data | versioned typed experiment protocol is separate from posterior belief | `experiment_protocol.json`, `belief_simulation_config.json`, and `value_provenance.json`; boundary tests reject truth fields in protocol |
| Reproducible media | fixed replay sampling, sequence/frame render barriers, WebGL crop validation | `docs/media/curious-robot-demo.gif`, MP4, metadata, and SHA-256 |

## Reference result

- Task outcome: completed successfully.
- Selected action: physical controlled push; pick-and-place was rejected because
  bilateral physical grasp has not passed validation.
- Static probe: 898 raw samples, 0.998 N peak horizontal contact force, and
  2.3 mm observed displacement. The released estimator accepts only native
  contact-valid samples and keeps the result interval-censored.
- Dynamic probe: 2,652 raw samples, 1.114 N peak horizontal contact force, and
  117.4 mm observed slide. The released estimator requires configured contact
  coverage and rejects non-contact/coasting samples.
- Final prediction residual: 18.6 mm; the cube entered the configured 35 mm
  target region and the task verified successfully.
- Event evidence: 1,976 committed events, including 80 decimated measurements,
  two belief updates, two belief-world rebuilds, native contact visuals, and
  1,816 authoritative Newton frames.
- Truth remains absent from the public event stream. Evaluator comparison is a
  separate terminal-only artifact.

## Honest limitations

- This is sim-to-sim, not a physical-robot experiment.
- The force producer uses Newton `SensorContact` over native MuJoCo Warp
  robot-tool ↔ cube contacts with a configured 10 Hz first-order sensor filter.
  It is not a complete strain-gauge wrist moment and excludes internal
  transmission loads.
- Static and dynamic coefficients are effective cube–surface interaction
  values. The MVP cannot identify separate intrinsic table and cube material
  coefficients.
- MuJoCo Warp contact is calibrated by SciPy black-box methods. No
  end-to-end differentiable-contact claim is made.
- The R3F robot uses the official Franka FR3 visual meshes, converted to a
  link-addressable GLB. It remains a presentation mirror driven exclusively by
  Newton body transforms and never advances browser-side physics.
- Re-execution is guaranteed only for the recorded hardware/software scope and
  explicit tolerance, not bitwise across GPU, driver, or solver changes. The
  latest boundary-validation trace passed at 0.752 mm maximum position error against a 15 mm
  tolerance; the numerical report remains separate from visual playback.
- Mass remains a prior because bilateral grasp/lift weighing is deliberately
  unavailable. The task did not require a mass claim: effective friction
  intervals remained broad, and dynamic Coulomb mismatch is recorded rather
  than replaced with hidden truth.

This is a complete research vertical slice, not a claim that every optional
extension in the master brief is production-ready. Off-center-mass
identification remains the next experiment-design extension.
