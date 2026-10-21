# Friction capability report

Generated from the v0.1.0 public reference run on Newton 1.7.0 / Warp 1.18.0.

## Result

- Success: `True`
- Terminal target error: `0.028079 m`
- Selected policy: `push_straight_low` at `0.040 m/s`
- Deterministic recovery bound: `2` attempts
- Reality attempts used: `1`
- Dynamic friction posterior: `0.468385 ± 0.189281`
- Policy protocol SHA-256: `76c3996914dd2d6ce36523ed8f58edc8506b76ad6abc5a96aa90654bc3971d9c`

## Authority isolation

The experiment Reality, learned Twin, fresh deployment Reality, and Evaluator
used distinct process and world identities. The experiment world was destroyed
before policy deployment.

## Content-addressed lineage

- `capability_gap`: `sha256:c1c3b001a37a3079874ea3b75e707fd3e354679d621b4f5bc247d70187588b34`
- `trajectory_dataset`: `sha256:36261823288970c75ea31ab130b9340bdbc84c45d5cb88f52d6608b5ee2f1eb3`
- `parameter_posterior`: `sha256:028720cf128cb7f1a11e9a07fd4d5d205469982e52246c2161002d9aa543637d`
- `world_spec_patch`: `sha256:06261f333dc50cb5e85980f126e5db8a5258f770530d1f533e73ed585f3f28a5`
- `twin_build`: `sha256:c2378b35eee12759b7f401da7f34ddc5ebc09bb94012bff3eb05cabab145b1a8`
- `twin_validation_report`: `sha256:95c3f99e2782c134b4649bfecc624355c88463a30dd1f332762e42dfb02fb430`
- `policy_artifact`: `sha256:6788d14f600b0bd03448f11c4deaa6125307327e76a7dc4ac3754c9a5a94a8fa`
- `reality_deployment`: `sha256:3578a188447ee1b5e3316c26338f203a7456b6b871858289a49059cc26b5ef16`
- `evaluation_report`: `sha256:d9fd6ea8cfa90b51690ecccac22ee90739bd48462c87a095c32be413e6505fe4`

## Presentation clearance

- Source run: `1f16dd40-7a31-46a9-bfab-0ef66569252e`
- Visual frames checked: `1749`
- Maximum Franka visual-mesh penetration: `0.527 mm`
- Frames exceeding the `1.0 mm` publication threshold: `0`

Hidden scenario parameters are intentionally absent. This file contains public
evidence, posterior state, and the terminal evaluator decision only.
