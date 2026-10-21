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
