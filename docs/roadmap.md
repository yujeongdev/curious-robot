# Roadmap

## Next: task and object catalog

Generalize the current cube capability without duplicating the laboratory
pipeline. The extension introduces three independent, versioned contracts:

- `TaskDefinition`: goal, predicates, action set, budgets, and requirements
- `ObjectArchetype`: public geometry, reference frames, support polygon, and
  contact regions
- `ExperimentDefinition`: applicability, protocol, evidence schema, quality
  gates, and estimator binding

JSON stores values and immutable IDs. An allowlisted Python registry binds
validated builders, controllers, estimators, and goal metrics. Episode-specific
truth remains private to Reality.

## Push T-bar

The first extension is a known compound T geometry with hidden mass, effective
friction, planar center of mass, and yaw inertia. The task requires planar pose
matching `(x, y, yaw)`, not position-only success.

Planned experiments include pulse/coast translation and paired off-center
pushes with opposite yaw excitation. Parameters that are not identifiable from
the available evidence remain bounded or unidentified. Full 3D inertia and
center-of-mass height are deferred until a physical grasp/lift or equivalent
sensor producer is validated.

## Dual-world Agent Lab

The first dual-world slice is complete. Agent Lab shows Simulated Reality and
the learned Newton Twin side by side on one causal event cursor. Each world
retains its own last frame, local simulation time, identity, scenario, camera,
and belief revision. New evidence is produced only in Reality; Twin rollouts
predict policy outcomes. Replay and media capture acknowledge both panes.

The next UI slice will consume the isolated laboratory workers' event bridge,
add task/attempt history, and render manifest-driven object geometry. Until
that bridge lands, the dual-world interface visualizes the existing integrated
episode stream and must not imply that browser rendering is a physics worker.

## Acceptance target

- identical public geometry in Reality, Twin, replay, and R3F
- native contact coverage for every constituent T-bar shape
- held-out position, yaw, and force validation
- explicit task/object/protocol/posterior identities in every policy artifact
- position ≤30 mm and yaw ≤5° in at least 8 of 10 evaluator-seeded episodes
- unsupported T-bar hardware deployment remains fail-closed
