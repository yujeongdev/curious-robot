"""CLI adapter for the independently callable public-prior setup service."""

from __future__ import annotations

from pathlib import Path

import click

from curious_robot.sim_setup import DEFAULT_SETUP_STEPS, MAX_SETUP_STEPS

from ..core import Operation, Report
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

    return setup_simulation(
        task_id=task,
        output_directory=output_directory(output, "sim"),
        device=device,
        steps=steps,
        include_robot=not no_robot,
    )
