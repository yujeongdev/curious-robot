"""One execution boundary around native Click commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import click

from .output import diagnostics_to_stderr, emit, not_implemented

type Report = dict[str, Any]

_FAILURE_CONTEXT_FIELDS = (
    "task_id",
    "pipeline",
    "output_directory",
    "manifest_path",
    "failure_manifest",
    "report_path",
)


def _failure_context(document: Mapping[str, Any]) -> dict[str, str]:
    return {key: value for key in _FAILURE_CONTEXT_FIELDS if isinstance(value := document.get(key), str)}


class CommandFailure(Exception):
    """Preserve evidence locations when a domain operation raises."""

    def __init__(self, cause: Exception, *, context: Mapping[str, Any], exit_code: Literal[1, 2] = 1) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.context = dict(context)
        self.exit_code = exit_code


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
        context = _failure_context(
            {
                target: ctx.params[source]
                for source, target in (("task", "task_id"), ("task_id", "task_id"), ("pipeline", "pipeline"))
                if source in ctx.params
            }
        )
        try:
            with diagnostics_to_stderr():
                report = super().invoke(ctx)
            context.update(_failure_context(report))
            if report["status"] == "passed":
                exit_code = 0
            else:
                exit_code = 1 if report.get("error", {}).get("code") == "runtime_error" else 2
            emit(report, as_json=as_json or not self.human_output)
        except Exception as error:
            exit_code = 1
            if isinstance(error, CommandFailure):
                context.update(_failure_context(error.context))
                exit_code = error.exit_code
                error = error.cause
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
            emit(report)
        ctx.exit(exit_code)
