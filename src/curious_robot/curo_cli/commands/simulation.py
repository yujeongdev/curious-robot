"""CLI adapter for the independently callable public-prior setup service."""

from __future__ import annotations

from pathlib import Path

import click

from curious_robot.sim_setup import DEFAULT_SETUP_STEPS, MAX_SETUP_STEPS

from ..core import CommandFailure, Operation, Report
from ..output import output_directory
from ..parameters import device_option, output_option, task_option


@click.group()
def sim() -> None:
    """Set up and inspect simulation worlds."""


@sim.command("setup", cls=Operation, params=[task_option(), device_option(), output_option()])
@click.option(
    "--steps",
    type=click.IntRange(1, MAX_SETUP_STEPS),
    default=DEFAULT_SETUP_STEPS,
    show_default=True,
    help="Number of smoke rollout steps.",
)
@click.option("--no-robot", is_flag=True, help="Explicit reduced scene without Franka.")
def setup(task: str, device: str, output: Path | None, steps: int, no_robot: bool) -> Report:
    """Build and step a public prior-only task world."""
    from curious_robot.sim_setup import setup_simulation
    from curious_robot.tasks import get_task_preset

    directory = output_directory(output, "sim")
    context = {"task_id": task, "output_directory": str(directory)}
    try:
        get_task_preset(task)
    except KeyError as error:
        raise CommandFailure(error, context=context, exit_code=2) from error
    try:
        return setup_simulation(
            task_id=task,
            output_directory=directory,
            device=device,
            steps=steps,
            include_robot=not no_robot,
        )
    except FileExistsError as error:
        raise CommandFailure(error, context=context, exit_code=2) from error
    except Exception as error:
        report_path = directory / "setup_report.json"
        if report_path.is_file():
            context["report_path"] = str(report_path)
        raise CommandFailure(error, context=context) from error
