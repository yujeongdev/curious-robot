# Friction capability report

Generated from the v0.1.0 public reference run on Newton 1.7.0 / Warp 1.18.0.

## Result

- Success: `True`
- Terminal target error: `0.044414 m`
- Selected policy: `push_straight_low` at `0.040 m/s`
- Deterministic recovery bound: `2` attempts
- Reality attempts used: `1`
- Peak deployment contact force: `4.068217 N`
- Dynamic friction posterior: `0.451244 ± 0.182327`
- Policy protocol SHA-256: `76c3996914dd2d6ce36523ed8f58edc8506b76ad6abc5a96aa90654bc3971d9c`

## Authority isolation

The experiment Reality, learned Twin, fresh deployment Reality, and Evaluator
used distinct process and world identities. The experiment world was destroyed
before policy deployment.

## Content-addressed lineage

- `capability_gap`: `sha256:c1c3b001a37a3079874ea3b75e707fd3e354679d621b4f5bc247d70187588b34`
- `trajectory_dataset`: `sha256:d8915bdb582b14235b0695d49b773a5b444148955b9a92aaa20ce354ae9b6d0e`
- `parameter_posterior`: `sha256:5e598fd67c4be4e77b2e5a71db069c1e9ed6aa10b101a2b3e06166a94599c64d`
- `world_spec_patch`: `sha256:538f60c8b55481709e5e7a9b888135c7ff1098d96e8cac0e5ea8704a66499f8f`
- `twin_build`: `sha256:321c7ee1e99e761bd19f20a4dd0e386dab891a78b44666ac4b264a804d578d2a`
- `twin_validation_report`: `sha256:3ba15fe7aa26e4b849f1b04609495071b1b696751ebbbc34b7daf04e9de8d59b`
- `policy_artifact`: `sha256:ac59b8fc3c55146441d5f5a3409983fbafeddcc881d5250924a0ab524c908d25`
- `reality_deployment`: `sha256:577ab1a55faf41fd91d861703b21ca71876e997f9d32d4b6268d5beb96220108`
- `evaluation_report`: `sha256:e3f2a070afa4971c8f6a886153c599c5be691b2b989194da75c2512cf4b00a47`

Hidden scenario parameters are intentionally absent. This file contains public
evidence, posterior state, and the terminal evaluator decision only.
