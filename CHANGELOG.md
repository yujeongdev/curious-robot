# Changelog

All notable changes to Curious Robot are documented here.

## [0.2.3] - 2026-10-22

### Added

- Measure exact T-shape overlap with maximum and RMS registered-corner error derived from public compound geometry.
- Show shape-registration precision in the CLI capability report and Agent Lab verification evidence.

### Changed

- Version the T-bar task controller as `1.2.0` and persist its 15 mm translation-stop fraction explicitly.
- Keep the validated single-pass yaw contact timing after experimental repeated corrections were shown to destabilize the asymmetric T-bar.

### Validation

- Retained CUDA seed: `7.91 mm` center error, `4.65°` yaw error, `13.46 mm` maximum corner error, and `9.80 mm` RMS corner error.

## [0.2.2] - 2026-10-22

### Added

- Randomize the T-bar's initial position and yaw within the validated `[-8°, -5°]` Franka reachability envelope.
- Correct yaw with bounded crossbar pushes, then re-observe settled pose and recover translation from the best opposing public contact face.

### Changed

- Record the actual sequence of contact surfaces used by the controller so randomized-start decisions remain inspectable.
- Persist settle timing, minimum planar travel, and opposing-face selection bounds in physical experiment protocol `1.2.0`.
- Version the T-bar controller preset as `1.1.0` so older one-attempt policies cannot silently execute the new yaw-correction and retry contract.
- Refresh Agent Lab media from a successful randomized-start Reality → Twin → Reality run.

### Fixed

- Make the final settled Newton pose authoritative so a T-bar that drifts out of tolerance cannot retain an earlier success result.

## [0.2.1] - 2026-10-22

### Fixed

- Render the `push-t-bar-v1` goal as the exact public T-bar footprint on the tabletop instead of a circular target, and require its geometry identity and compound parts to match the manipulated object.
- Regenerate the Agent Lab replay and release media from a completed run that visibly aligns the physical T-bar with the T-shaped goal pose.

## [0.2.0] - 2026-10-22

### Added

- Run the new `push-t-bar-v1` task end to end: Franka performs bounded friction experiments in simulated Reality, updates a learned Newton Twin, compares three independent candidate worlds, and deploys the selected push into a fresh Reality episode.
- Inspect the full causal chain in Agent Lab with separate Reality and Twin panes, a five-step authority bridge, a three-slot candidate fleet, task selection, compound T-bar geometry, and pose-aware telemetry.
- Define versioned, data-driven object, task, contact-surface, experiment, goal, and randomization contracts for cube and T-bar extensions.

### Changed

- Bind contact actions to validated public object faces and reject out-of-face offsets or heights before robot motion.
- Record object orientation, angular motion, wrapped-yaw task predicates, and position/yaw re-execution comparisons.
- Treat expected controller reachability failures as candidate evidence while allowing infrastructure errors to fail the pipeline.

### Fixed

- Keep Reality experiments, independent Twin candidate worlds, and fresh Reality deployment identities distinct through policy validation and replay.
- Replace the penetrating demo with a task-bound T-bar recording gated by compound triangle-to-box clearance checks and immutable run provenance.
- Fail the friction CLI when terminal evaluation fails, and report contact-dynamics yaw nondeterminism without conflating it with visual playback.
