# Architecture and physical boundaries

Curious Robot separates the source of observations from the model used to make
predictions. `TruthEnvironment` is the current simulated **reality plant**: it
owns randomized `PhysicalParameters` and is reachable through robot commands
and hardware-equivalent observations. `BeliefEnvironment` is rebuilt
from posterior means; no Newton model, material array, or hidden value is copied
from truth to belief.

```text
private evaluator truth ──> Newton/MuJoCo Warp reality
                                  │ sensor observations
                                  ▼
 typed RobotTools ──> active planner ──> bounded experiment controller
       │                                      │
       └── evidence ──> SysID ──> belief ─────┘
                                  │
                                  ▼
                    independent Newton belief simulation
                                  │ push/pick rollouts
                                  ▼
                     selected action → reality → verify
```

## Physics ownership

- MuJoCo Warp owns Franka articulation, gravity, rigid body integration, and
  native contact.
- Native surface `mu` represents static breakaway. A velocity-hysteretic
  correction applied through `State.body_f` reduces sliding resistance to the
  independently randomized dynamic coefficient.
- Joint commands and high-level tool targets are distinct from measured force.
  The tool force producer is Newton `SensorContact` over MuJoCo Warp's native
  `CONTACT_F` observable for robot-tool ↔ cube contact pairs.
- The validated experiment path uses a visible spherical tool tip rigidly
  mounted at the Franka TCP. Its emitted radius and pose are shared with the
  Agent Lab mirror, and it is the only robot shape authorized to contact the
  cube. The controller moves Franka joints; no public action applies a force
  directly to the cube.
- Bilateral finger grasp/lift has not passed its physical acceptance gate.
  Pick-and-place and weighing are therefore reported unavailable instead of
  using a weld, teleport, or cube-force surrogate.

## Agent ownership

The deterministic planner maximizes relevance-weighted Gaussian entropy
reduction minus duration, cost, and risk. Safety is a hard filter. An optional
OpenAI adapter can select only among validated high-level experiments; it never
controls a physics timestep.

The parameter estimators consume immutable evidence records. Static friction is
interval-censored from measured hold/break forces. Dynamic friction is fit with
bounded robust SciPy least squares, separately from static friction. MuJoCo Warp
contact is not claimed to be differentiable.

## Hardware replacement seam

Replacing the reality plant requires an implementation of the same typed
observation and bounded-action surface. SysID, beliefs, planning, replay schema,
and belief simulation remain unchanged. No ROS 2 dependency is introduced in
this simulation-only milestone.

The complete ownership and value-flow contract is
[`specs/2026-10-21-reality-sysid-belief-boundary.md`](specs/2026-10-21-reality-sysid-belief-boundary.md).

## Laboratory pipeline spine

The next execution architecture is defined by `curious_robot.lab`. It separates
three high-level ports instead of treating the current Newton runner as one
trusted object:

- `RealityPort` accepts the shared operation schema and returns observation
  artifacts. Its hidden configuration, model, and state are absent from the
  agent-facing contract.
- `TwinPort` builds a task-oriented world from a versioned `WorldSpec`. A
  `RuntimeIdentity` gate is applied by the trusted coordinator and rejects any
  Reality/Twin pair that shares a process, world, model, state, or
  configuration identity. Spawn-based tests bind the reported PID to each
  worker's actual OS process.
- `EvaluatorPort` accepts only a terminal artifact. Evaluator artifacts live in
  a separate authority root and cannot be read or referenced by agent artifacts.
  Agent workers receive only the handle returned by `open_agent_artifact_store`;
  the two-store resolver belongs exclusively to the trusted coordinator.

Artifacts are canonical JSON addressed by SHA-256 over the complete envelope,
including provenance, validation conditions, visibility, and PASS/FAIL outcome.
A failed validation remains evidence rather than disappearing as an exception.
`WorldSpecPatch` carries its base content ID, so applying an observation-derived
patch to a newer twin specification fails instead of silently overwriting it.
Only `model_parameters` may be patched, agent-visible PASS evidence is required,
and the resulting spec retains both evidence refs and applied patch IDs.

The pipeline state machine resolves every evidence ref from CAS, checks its
integrity, kind, visibility, outcome, and causal CAS lineage before transition. It publishes
one of four stable lanes: `reality`, `twin`, `deployment`, or
`capability_build`. A validated policy can reach Reality only after Twin build
and validation; evaluation occurs only after a fresh Reality deployment artifact.

The `lab-friction` runner is the first end-to-end implementation of this
pipeline. One persistent Reality service owns hidden parameters and allocates
separate experiment and deployment `TruthEnvironment` instances. A different
process builds and validates `NewtonLearnedTwin` only from the posterior
`WorldSpec`; a third process evaluates the terminal deployment. The trusted
coordinator owns CAS and the FSM but receives no sampled hidden physical
parameters. Normal runs sample the scenario inside Reality. In explicit
evaluator-reproducibility mode the launcher passes the private seed only to
Reality and scrubs it from every child environment.
Reality and Twin receive only agent-store handles. The evaluator receives
separate read-agent/write-evaluator handles. Root-level manifest/report output
is an explicit trusted-user terminal summary and is never fed back to the agent.
The WorldSpec and PolicyArtifact both carry the exact versioned physical
experiment protocol snapshot and SHA-256; Twin validation and fresh Reality
deployment reject a mismatched snapshot.

The legacy multi-experiment `run_episode` path has not yet migrated to these
workers. It remains available for the Agent Lab demo and must not be cited as
process-isolated. This distinction keeps the completed friction slice from
overstating the older runner's authority boundary.

The full contract and migration order are documented in
[`specs/2026-10-21-self-building-lab-spine.md`](specs/2026-10-21-self-building-lab-spine.md).
