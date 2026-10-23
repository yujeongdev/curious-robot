# Curious Robot documentation map

Public documentation is organized by purpose rather than implementation date.

| 갈래 | 위치 | 역할 |
| --- | --- | --- |
| Architecture | `architecture.md` | Reality/Twin/Evaluator boundaries and data flow |
| Agent Lab | `agent-lab.md` | UI, event, replay, and capture contracts |
| Design specs | `designs/` | implementation-facing UI boundaries and interaction rules |
| Engineering specs | `specs/` | versioned decisions for execution, deployment, and sensor contracts |
| Plans | `plans/` | ordered implementation slices and acceptance gates |
| Research | `research/` | external ecosystem findings kept separate from repository decisions |
| Usage guides | `guides/` | task selection, CLI operations, and agent workflows |
| Reproducibility | `reproducibility.md` | playback and re-execution semantics |
| Validation | `validation.md` | executed capability and honest limits |
| Roadmap | `roadmap.md` | task/object catalog and T-bar extension |
| Evidence | `evidence/`, `media/` | compact numerical reports and public media |

Machine-readable Newton/Warp capability references are pinned in
`capabilities.json`. Generated run directories are intentionally excluded from
Git; only selected, reviewable evidence is published here.

## Next development plan

[Autonomous physics discovery and two-robot bowling](plans/2026-10-24-autonomous-physics-discovery-and-bowling.md)
is the entry point for the proposed next extension. It links the architecture
decisions and primary-source research, and separates simulated milestones from
physical hardware acceptance.

[Curo task/CLI architecture](specs/2026-10-24-task-loading-and-curo-cli.md) defines
task loading and the staged command surface. The [agent workflow guide](guides/curo-agent-workflow.md)
and [repository skill](../.agents/skills/curo/SKILL.md) explain how to use the actual
implemented commands and recognize pending operations.
