"""One execution boundary around native Click commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from .output import diagnostics_to_stderr, emit, not_implemented

type Report = dict[str, Any]


class CommandFailure(Exception):
    """Preserve evidence locations when a domain operation raises."""

    def __init__(self, cause: Exception, *, context: Mapping[str, Any]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.context = dict(context)


class Operation(click.Command):
    """Keep availability, JSON output and exit semantics consistent for each leaf."""

    def __init__(
        self,
        name: str | None = None,
        *,
        params: list[click.Parameter] | None = None,
        implementation_hint: str | None = None,
        human_output: bool = False,
        **kwargs: Any,
    ) -> None:
        parameters = list(params or ())
        parameters.append(click.Option(["--json"], is_flag=True, help="Emit a machine-readable result."))
        super().__init__(name, params=parameters, **kwargs)
        self.implementation_hint = implementation_hint
        self.human_output = human_output

    @property
    def implemented(self) -> bool:
        return self.callback is not None

    def invoke(self, ctx: click.Context) -> None:
        as_json = ctx.params.pop("json")
        if not self.implemented:
            emit(not_implemented(ctx.command_path, self.implementation_hint or ""), as_json=as_json)
            ctx.exit(3)
        context = {
            target: ctx.params[source]
            for source, target in (("task", "task_id"), ("task_id", "task_id"), ("pipeline", "pipeline"))
            if source in ctx.params
        }
        try:
            with diagnostics_to_stderr():
                report = super().invoke(ctx)
            exit_code = 0 if report["status"] == "passed" else 2
        except Exception as error:
            if isinstance(error, CommandFailure):
                context.update(error.context)
                error = error.cause
            exit_code = 2 if isinstance(error, (KeyError, ValueError, TypeError, OSError)) else 1
            report = {
                "schema_version": "curo-command-v1",
                "command": ctx.command_path,
                "status": "failed",
                **context,
                "error": {
                    "code": "command_failed" if exit_code == 2 else "runtime_error",
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        emit(report, as_json=as_json or not self.human_output)
        ctx.exit(exit_code)
