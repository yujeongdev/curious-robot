"""Compose lightweight Click command modules without loading a physics backend."""

from __future__ import annotations

import click

from .commands.pending import register_pending
from .commands.runs import run
from .commands.simulation import sim
from .commands.tasks import capabilities, tasks
from .commands.web import open_web


def build_cli() -> click.Group:
    cli = click.Group("curo", help="Select a task. Set up simulation. Run discovery.")
    for command in (capabilities, tasks, run, sim, open_web):
        cli.add_command(command)
    register_pending(cli)
    return cli
