"""Discover capabilities directly from the native Click command tree."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import click

from .core import Operation


def iter_commands(
    group: click.Group, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Operation]]:
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, click.Group):
            yield from iter_commands(command, path)
        else:
            if not isinstance(command, Operation):
                raise TypeError(f"curo {' '.join(path)} must use the Operation command boundary")
            yield path, command


def command_document(path: tuple[str, ...], command: Operation) -> dict[str, Any]:
    return {
        "command": "curo " + " ".join(path),
        "status": "implemented" if command.implemented else "not_implemented",
        "summary": command.help or "",
        "arguments": [
            {
                **parameter.to_info_dict(),
                "names": (
                    [*parameter.opts, *parameter.secondary_opts]
                    if isinstance(parameter, click.Option)
                    else [parameter.name]
                ),
            }
            for parameter in command.params
            if parameter.name != "json"
        ],
        "json_option": "--json",
        "implementation_hint": command.implementation_hint,
    }
