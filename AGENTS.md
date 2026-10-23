# Agent instructions

## Design System

Always read `DESIGN.md` before making any visual or UI decisions.
All font choices, colors, spacing, motion, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any UI code that does not match `DESIGN.md`.

For Agent Lab work, also read `docs/agent-lab.md`. R3F is a presentation mirror of Newton state and must never become a browser physics authority.

## Curo task operations

Use the repository skill at [.agents/skills/curo/SKILL.md](.agents/skills/curo/SKILL.md)
for task selection/loading, simulation setup, and discovery runs. Inspect
`curo capabilities --json` and task help before automating pipeline operations;
pending commands return `not_implemented` and must not be treated as successful.
Cube and T-bar are catalog tasks, not separate application modes.
