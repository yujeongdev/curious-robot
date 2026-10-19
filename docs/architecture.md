# Architecture and physical boundaries

Curious Robot separates the source of observations from the model used to make
predictions. `TruthEnvironment` owns randomized `PhysicalParameters` and is
reachable by the agent only through `RobotTools`. `BeliefEnvironment` is rebuilt
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
  The MVP wrench is the bounded actuator reaction with noise and explicit
  provenance. It never reads object mass or friction.
- The pick controller is a compliant spring-force grasp surrogate. It applies
  bounded forces to the dynamic cube and never teleports or welds it. The
  Franka URDF, articulation, and gripper are present and stepped, while a future
  milestone will replace the grasp surrogate with bilateral finger contacts.

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

Replacing `TruthEnvironment` requires an implementation of the same typed
observation and bounded-action surface. SysID, beliefs, planning, replay schema,
and belief simulation remain unchanged. No ROS 2 dependency is introduced in
this simulation-only milestone.
