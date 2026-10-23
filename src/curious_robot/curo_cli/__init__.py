"""Task-oriented Click CLI; domain and GPU imports remain inside operations."""

from __future__ import annotations

import click

from .app import build_cli


def main(argv: list[str] | None = None) -> None:
    try:
        exit_code = build_cli().main(args=argv, prog_name="curo", standalone_mode=False)
    except click.ClickException as error:
        error.show()
        raise SystemExit(error.exit_code) from error
    except click.Abort as error:
        click.echo("Aborted!", err=True)
        raise SystemExit(1) from error
    if exit_code:
        raise SystemExit(exit_code)
