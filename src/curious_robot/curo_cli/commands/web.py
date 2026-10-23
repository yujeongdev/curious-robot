"""Keep the existing local Agent Lab server behind task-oriented CLI syntax."""

from __future__ import annotations

import click

from ..core import Operation, Report
from ..parameters import device_option, task_option


@click.command("web", cls=Operation, params=[task_option(), device_option()])
@click.option("--seed", type=int, default=7, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8765, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True)
@click.option("--autorun", is_flag=True, help="Explicitly start the loaded task.")
def open_web(
    task: str, device: str, seed: int, host: str, port: int, open_browser: bool, autorun: bool
) -> Report:
    """Open Agent Lab with a task selected."""
    from curious_robot.cli import main as legacy_main
    from curious_robot.tasks import get_task_preset

    get_task_preset(task)
    arguments = [
        "web",
        "--task",
        task,
        "--host",
        host,
        "--port",
        str(port),
        "--device",
        device,
        "--seed",
        str(seed),
        "--open" if open_browser else "--no-open",
    ]
    if autorun:
        arguments.append("--autorun")
    try:
        legacy_main(arguments)
    except SystemExit as error:
        if error.code == 2:
            raise ValueError("Agent Lab rejected its launch arguments; see stderr for details") from error
        message = (
            error.code
            if isinstance(error.code, str)
            else f"Agent Lab terminated during startup (exit {error.code}); see stderr for details"
        )
        raise RuntimeError(message) from error
    return {"schema_version": "curo-command-v1", "command": "web", "status": "passed", "task_id": task}
