"""Shared native Click parameters; no parallel argument schema."""

from __future__ import annotations

from pathlib import Path

import click


def task_option() -> click.Option:
    return click.Option(["--task"], required=True, help="Registered task preset ID.")


def device_option() -> click.Option:
    return click.Option(["--device"], default="cuda:0", show_default=True)


def output_option() -> click.Option:
    return click.Option(
        ["--output"],
        type=click.Path(path_type=Path),
        help="New output directory; defaults to a unique artifacts/curo path.",
    )


def setup_option() -> click.Option:
    return click.Option(["--setup"], required=True, help="Simulation setup artifact path.")


def bundle_option() -> click.Option:
    return click.Option(["--bundle"], required=True, help="Deployment bundle path.")
