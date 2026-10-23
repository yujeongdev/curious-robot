"""Public task discovery without importing a physics runtime."""

from __future__ import annotations

import click

from ..core import CommandFailure, Operation, Report


def task_document(task_id: str) -> Report:
    from curious_robot.tasks import resolve_task

    return resolve_task(task_id).document()


@click.command(cls=Operation, human_output=True)
@click.pass_context
def capabilities(ctx: click.Context) -> Report:
    """List command availability for people and agents."""
    from ..registry import command_document, iter_commands

    root = ctx.find_root().command
    if not isinstance(root, click.Group):
        raise TypeError("capability discovery requires a Click command group")
    return {
        "schema_version": "curo-capabilities-v1",
        "status": "passed",
        "commands": [command_document(path, command) for path, command in iter_commands(root)],
        "exit_codes": {"passed": 0, "runtime_error": 1, "failed": 2, "not_implemented": 3},
    }


@click.group()
def tasks() -> None:
    """Select and inspect registered tasks."""


@tasks.command("list", cls=Operation, human_output=True)
def list_tasks() -> Report:
    """List registered public task presets."""
    from curious_robot.tasks import list_task_presets

    return {
        "schema_version": "curo-tasks-v1",
        "status": "passed",
        "tasks": [task_document(preset.preset_id) for preset in list_task_presets()],
    }


@tasks.command("show", cls=Operation, human_output=True)
@click.argument("task_id")
def show_task(task_id: str) -> Report:
    """Inspect a task, goal and public geometry."""
    try:
        document = task_document(task_id)
    except KeyError as error:
        raise CommandFailure(error, context={"task_id": task_id}, exit_code=2) from error
    return {"schema_version": "curo-task-v1", "status": "passed", **document}
