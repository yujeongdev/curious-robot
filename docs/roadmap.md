# Roadmap

## Delivered: task and object catalog

The cube and T-bar tasks now share three independent, versioned contracts:

- `TaskDefinition`: goal, predicates, action set, budgets, and requirements
- `ObjectArchetype`: public geometry, reference frames, support polygon, and
  contact regions
- `PhysicalExperimentProtocol`: versioned probe settings and evidence quality
  gates, with validated task-specific overrides

JSON stores values and immutable IDs. An allowlisted Python registry binds
validated builders, controllers, estimators, and goal metrics. Episode-specific
truth remains private to Reality.

## Delivered: first Push T-bar slice

The first extension uses known compound T geometry with hidden effective mass
and friction. It requires planar position plus yaw preservation, evaluates
three independent contact-plan candidates in learned Newton worlds, and deploys
the selected plan into fresh simulated Reality.

Next, the off-center-mass extension will relax the public uniform-density COM
assumption. Paired pushes with opposite yaw excitation will then identify
planar COM and yaw inertia. Parameters that are not identifiable from available
evidence remain bounded or unidentified. Full 3D inertia and center-of-mass
height stay deferred until a physical grasp/lift or equivalent sensor producer
is validated.

## Dual-world Agent Lab

Agent Lab shows the Reality stand-in and learned Newton Twin side by side on one
causal event cursor. A five-step causal bridge shows experiment, observation,
posterior patch, candidate validation, and fresh deployment. A three-slot fleet
shows only recorded candidate worlds and leaves unused capacity explicit. Each world
retains its own last frame, local simulation time, identity, scenario, camera,
and belief revision. New evidence is produced only in Reality; Twin rollouts
predict policy outcomes. Replay and media capture acknowledge both panes.

Public compound geometry now travels in visual events, so R3F mirrors the
recorded cube or T-bar without owning physics. The next UI slice will consume
the isolated laboratory workers' event bridge and add task/attempt history.

## Acceptance target

- identical public geometry in Reality, Twin, replay, and R3F
- native contact coverage for every constituent T-bar shape
- held-out position, yaw, and force validation
- explicit task/object/protocol/posterior identities in every policy artifact
- current MVP: position ≤30 mm and yaw ≤8° in one retained evaluator-seeded run
- research target: position ≤30 mm and yaw ≤5° in at least 8 of 10 evaluator-seeded episodes
- unsupported T-bar hardware deployment remains fail-closed
