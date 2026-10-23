# Contributing

Thanks for helping improve Curious Robot. Changes should preserve the
scientific boundary between hidden Reality, observed evidence, posterior
belief, and the independently constructed Newton Twin.

## Development setup

```bash
uv sync --extra dev --extra web
corepack pnpm --dir src/web install --frozen-lockfile
```

Python checks:

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest -m "not gpu"
```

The strict type check covers the Curo CLI, simulation setup service, and shared
task description boundary. Warp's untyped runtime calls are excluded; project
callbacks remain checked. For CLI extensions, follow the
[native Click command procedure](docs/specs/2026-10-24-task-loading-and-curo-cli.md#4-cli-코드를-나누는-기준).

Frontend checks:

```bash
corepack pnpm --dir src/web lint
corepack pnpm --dir src/web typecheck
corepack pnpm --dir src/web test
corepack pnpm --dir src/web build
diff -qr src/web/dist src/curious_robot/web_static
```

Run GPU tests serially on a supported NVIDIA workstation:

```bash
uv run pytest -m gpu
```

## Pull requests

- Keep each PR focused on one observable outcome.
- Add behavior-level tests for every new branch and failure path.
- Use typed, versioned contracts for observations, protocols, and artifacts.
- Put tunable values in validated configuration; keep safety invariants in
  code with named constants and evidence.
- Do not expose hidden episode parameters through agent APIs, events, logs, or
  public artifacts.
- Do not substitute direct object forces, teleports, or welds for robot action.
- Keep React Three Fiber presentation-only; Newton owns physics.
- Preserve partial evidence when an experiment or run fails.

Generated run directories belong under `artifacts/` and must not be committed.
Publish large videos as release assets. Commit only compact evidence required to
audit a claim.

## Commit style

Use an imperative subject with an optional conventional prefix:

```text
feat: add a validated experiment
fix(web): retain frames by world identity
docs: clarify the replay boundary
```

Explain scientific assumptions and numerical tolerances in the PR body.
