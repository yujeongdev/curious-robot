---
name: curo
description: "Operate Curious Robot through its task catalog and curo CLI. Use when selecting or loading robotics tasks, setting up Newton simulations, running physics discovery, or inspecting which pipeline operations are implemented in the curious-robot project."
---

# Curo task operations

Use the project's CLI and evidence contracts to carry out the requested task. Cube and T-bar are task examples; choose the task that matches the goal instead of assuming either one.

## Discover before execution

Work from the repository or an installed Curo environment. Read the repository's `AGENTS.md`. Inspect the actual interface:

```bash
uv run curo capabilities --json
uv run curo tasks list --json
uv run curo tasks show push-cube-v1 --json
```

Use `--help` on the relevant command to confirm supported inputs. Help and capabilities come from the same native Click command tree. Command presence is not evidence of implementation: capabilities distinguish callbacks connected to services from pending operations. Do not import Python internals to bypass a command's unsupported or rejected state.

If `curo` is missing in a checkout that declares its entrypoint, synchronize the existing project environment. On an older checkout, inspect `uv run curious-robot --help` and report its different interface; do not invent equivalent commands. `curious-robot run` is the legacy pipeline, while canonical `curo run` defaults to the isolated pipeline.

## Load, set up, run

- To load a task into Agent Lab, use `curo web --task TASK`. This selects the task without starting an experiment. Use explicit `--autorun` only when automatic execution is requested.
- For setup, use `curo sim setup --task TASK --device DEVICE --output DIRECTORY --json`. Read the returned report and checks. The current setup builds and steps a public-prior world; it does not demonstrate calibrated physics or a successful task.
- For an authorized discovery run, use `curo run --task TASK --pipeline isolated --device DEVICE --output DIRECTORY --json`. Choose a fresh output directory and keep the returned manifest/evidence identifiers.
- Use `--pipeline legacy` only when the requested behavior requires that path; identify it in the result. Hardware operations require their own implemented adapter and admission, not a simulated substitute.

Choose task/device/output from the request and actual catalog/environment. Ask only for missing consequential choices. Available task defaults are public priors, not episode truth. Inspect relevant JSON result fields rather than treating exit zero as proof of physical success.

## Pending operations and failures

The CLI exposes assets, experiments, SysID, models, policy, run management, artifacts, replay, diagnosis, and hardware command families as the implementation grows. Discover their current status rather than assuming every family works.

`status=not_implemented` and exit code 3 mean no domain work was performed. Report the missing operation and its hint; do not fabricate an artifact, silently run a different pipeline, or claim the requested stage succeeded. If development is requested, implement the application service, register a typed native Click callback with `cls=Operation`, and remove the matching registration from `commands/pending.py`. Verify behavior and the automatically derived capability status; do not add a parallel descriptor, parser, or manual status flag. See the CLI spec for the extension procedure.

Use JSON stdout for automation and stderr for diagnostics. Click input errors use stderr and exit 2, so check both the exit code and whether a result exists. Retain failed reports and task/model/geometry/runtime identities. Read setup state and observation checks independently; finite stepping alone does not prove an observation succeeded. Limit retries to changes justified by the reported cause and the task's budget. Never expose private Reality parameters, seeds, or evaluator-only artifacts to the discovery planner.

## Detailed guidance

Resolve these paths from the repository root:

- `docs/guides/curo-agent-workflow.md` — current commands, report interpretation, and examples.
- `docs/specs/2026-10-24-task-loading-and-curo-cli.md` — command/service boundaries and migration.
- `docs/specs/2026-10-24-experiment-driven-twin-construction.md` — physics/setup/model-selection contracts.

For UI work, also read `DESIGN.md` and `docs/agent-lab.md`. R3F renders Newton's recorded state and never owns physics.
