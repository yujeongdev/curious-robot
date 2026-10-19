# Agent Lab goal audit

This audit maps the Curious Robot MVP goal to executable evidence. The
reference portfolio run is `043d2785-9ab3-452e-966a-1c66760522a0` (seed 7,
Franka enabled, hidden wood-family surface). It completed successfully on an
RTX 4080 SUPER with Newton 1.7.0, Warp 1.18.0, and MuJoCo 3.15.0.

| Goal | Implementation | Verification / evidence |
| --- | --- | --- |
| Hidden simulated reality | `TruthEnvironment`; evaluator-only `PhysicalParameters` and `SurfaceProfile` | truth-boundary tests; evaluator artifacts are terminal-gated |
| Independent belief simulation | `BeliefEnvironment.from_belief()` rebuilds a new Newton model | three `simulation.belief_rebuilt` events with distinct world IDs in the reference run |
| Active experiment choice | deterministic EIG − cost − risk planner with safety filtering | rationale events and planning tests |
| Physical identification | quasi-static mass, breakaway interval, robust dynamic-friction fit | 120 decimated sensor events, three measurement summaries, three posterior updates |
| Push versus pick decision | both candidates roll out under the same belief; multi-term utility selects | reference run selected pick-and-place after two prediction events |
| Execute and verify | bounded controller executes only after selection | final target success; prediction residual recorded in `result.json` |
| Live evidence UI | FastAPI/SSE plus React reducer, R3F presentation mirror, stage rail, evidence and telemetry | frontend unit/E2E tests and actual-run screenshot/video |
| Visual replay | Newton `ViewerFile` recording and Agent Lab event replay | `scene_replay.bin`; replay slider uses the same reducer as live mode |
| Physics re-execution | exact recorded low-level force commands applied to a fresh world | `reexecution.json` and explicit `reexecution_report.json`; failures remain visible |
| Reproducible media | fixed replay sampling, sequence/frame render barriers, WebGL crop validation | `docs/media/curious-robot-demo.gif`, MP4, metadata, and SHA-256 |

## Reference result

- Task outcome: completed successfully.
- Selected action: pick-and-place.
- Event evidence: 967 committed events, including 120 sampled measurements,
  three belief updates, three belief-world rebuilds, and hundreds of Newton
  visual frames.
- Truth remains absent from the public event stream. Evaluator comparison is a
  separate terminal-only artifact.
- Exact low-level re-execution was attempted on the same GPU and package set:
  942 samples, 0.0487 m RMSE, 0.1378 m maximum position error versus the
  articulated tolerance of 0.015 m. The report is a transparent FAIL, not
  confused with successful visual state playback.

## Honest limitations

- This is sim-to-sim, not a physical-robot experiment.
- The virtual wrench producer measures the bounded external actuator path with
  configured noise; it is not a strain-gauge wrist sensor and excludes internal
  transmission loads.
- Static and dynamic coefficients are effective cube–surface interaction
  values. The MVP cannot identify separate intrinsic table and cube material
  coefficients.
- MuJoCo Warp contact is calibrated by SciPy black-box methods. No
  end-to-end differentiable-contact claim is made.
- The R3F robot uses the official Franka FR3 visual meshes, converted to a
  link-addressable GLB. It remains a presentation mirror driven exclusively by
  Newton body transforms and never advances browser-side physics.
- Exact low-level re-execution can exceed tolerance because fresh public state
  reconstruction does not restore all solver-private contact warm-start state.
  The numerical report is published rather than relabeled as deterministic
  playback.

This is a complete research vertical slice, not a claim that every optional
extension in the master brief is production-ready. Off-center-mass
identification remains the next experiment-design extension.
