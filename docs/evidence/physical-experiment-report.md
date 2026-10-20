# Robot physical experiment report

- Run seed: `7`
- Reality boundary: hidden-parameter Newton plant standing in for the future physical workspace
- Agent boundary: robot commands, camera/object pose, joint/FK state, and native contact force only
- Belief boundary: independently constructed Newton model from posterior estimates

## Experiments

### static_friction — `static-2cf88d3e`

- Producer: Newton SensorContact hand/fingers/pusher ↔ cube
- Controller: Franka IK joint-target contact ramp
- Raw samples: 892
- Accepted native-contact samples: 47
- Rejected non-contact samples: 845
- Contact coverage: 5.3%
- Peak measured horizontal contact force: 2.358 N
- Observed horizontal displacement: 0.0024 m
- Quality flags: none

### dynamic_friction — `dynamic-9914969f`

- Producer: Newton SensorContact hand/fingers/pusher ↔ cube
- Controller: Franka IK joint-target maintained-contact slide
- Raw samples: 1212
- Accepted native-contact samples: 436
- Rejected non-contact samples: 776
- Contact coverage: 36.0%
- Peak measured horizontal contact force: 2.952 N
- Observed horizontal displacement: 0.0730 m
- Quality flags: none

## Posterior

- `mass_kg`: 0.2650 ± 0.2097 kg; status=prior; method=bounded uniform prior
- `mu_static`: 0.6497 ± 0.2833 1; status=bounded; method=interval-censored measured breakaway force / normal load
- `mu_dynamic`: 0.4603 ± 0.4900 1; status=bounded; method=bounded robust least squares: F = m a + mu N sign(v); mass-posterior-derived normal load
  - Model mismatch: Coulomb residual is structured or larger than the validation threshold
- `com_x_m`: 0.0000 ± 0.0098 m; status=prior; method=bounded uniform prior
- `com_y_m`: 0.0000 ± 0.0098 m; status=prior; method=bounded uniform prior
- `com_z_m`: 0.0000 ± 0.0059 m; status=prior; method=bounded uniform prior

## Decision and verification

- Selected action: `push_straight_low`
- Task success: `True`
- Prediction residual: 0.1309 m

> Ground-truth values are intentionally excluded from this agent-facing report. They exist only in terminal evaluator artifacts.
