"""Adapters for existing discovery pipelines; pipeline identity stays explicit."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..core import CommandFailure, Operation, Report
from ..output import output_directory
from ..parameters import device_option, output_option, task_option

_RUN_RESERVATION = ".curo-run.json"


def _reserve_run_directory(directory: Path, *, task: str, pipeline: str) -> None:
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise FileExistsError(
            f"run output is not an empty directory: {directory}; choose a new output directory"
        )
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents two processes claiming the same empty directory.
    with (directory / _RUN_RESERVATION).open("x", encoding="utf-8") as reservation:
        json.dump({"task_id": task, "pipeline": pipeline}, reservation)
        reservation.write("\n")


@click.command(
    cls=Operation,
    params=[task_option(), device_option(), output_option()],
)
@click.option("--seed", type=int, default=7, show_default=True)
@click.option("--pipeline", type=click.Choice(("isolated", "legacy")), default="isolated", show_default=True)
@click.option("--no-robot", is_flag=True, help="Legacy reduced scene only.")
def run(task: str, device: str, output: Path | None, seed: int, pipeline: str, no_robot: bool) -> Report:
    """Run discovery for an explicitly selected task."""
    from curious_robot.tasks import get_task_preset

    get_task_preset(task)
    if no_robot and pipeline == "isolated":
        raise ValueError("--no-robot requires --pipeline legacy; isolated discovery requires Franka")
    directory = output_directory(output, "run")
    context = {"task_id": task, "pipeline": pipeline, "output_directory": str(directory)}
    try:
        _reserve_run_directory(directory, task=task, pipeline=pipeline)
    except OSError as error:
        raise CommandFailure(error, context=context) from error
    try:
        return _execute_pipeline(
            task=task,
            device=device,
            output=directory,
            seed=seed,
            pipeline=pipeline,
            include_robot=not no_robot,
        )
    except Exception as error:
        manifest = directory / "friction_capability_manifest.json"
        if pipeline == "isolated" and manifest.is_file():
            context["failure_manifest"] = str(manifest)
        raise CommandFailure(error, context=context) from error


def _execute_pipeline(
    *, task: str, device: str, output: Path, seed: int, pipeline: str, include_robot: bool
) -> Report:
    details: Report = {}
    if pipeline == "isolated":
        from curious_robot.lab.friction_artifacts import CapabilityExecutionError
        from curious_robot.lab.friction_runner import run_newton_friction_slice

        manifest = output / "friction_capability_manifest.json"
        try:
            run_newton_friction_slice(task_id=task, seed=seed, device=device, output_directory=output)
        except CapabilityExecutionError as error:
            success = False
            details["error"] = {
                "code": "validation_failed",
                "type": type(error).__name__,
                "message": str(error),
            }
            result = json.loads(manifest.read_text()) if manifest.is_file() else None
            if manifest.is_file():
                details["failure_manifest"] = str(manifest)
        else:
            result = json.loads(manifest.read_text())
            success = result.get("status") == "PASSED"
    else:
        from curious_robot.runner import run_episode

        episode = run_episode(
            task_id=task, seed=seed, output_directory=output, device=device, include_robot=include_robot
        )
        result = episode.model_dump(mode="json")
        success = episode.success
    return {
        "schema_version": "curo-command-v1",
        "command": "run",
        "task_id": task,
        "pipeline": pipeline,
        "status": "passed" if success else "failed",
        "output_directory": str(output),
        "result": result,
        **details,
    }
