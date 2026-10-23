"""Unimplemented pipeline stages expressed as ordinary Click commands."""

from __future__ import annotations

import click

from ..core import Operation
from ..parameters import bundle_option, device_option, setup_option, task_option


def _pending(name: str, summary: str, hint: str, *params: click.Parameter) -> Operation:
    return Operation(name, help=summary, implementation_hint=hint, params=list(params))


def _group(name: str, *commands: Operation) -> click.Group:
    return click.Group(name, commands=commands)


def register_pending(cli: click.Group) -> None:
    tasks = cli.commands["tasks"]
    sim = cli.commands["sim"]
    if not isinstance(tasks, click.Group) or not isinstance(sim, click.Group):
        raise TypeError("task and simulation command families must be Click groups")
    tasks.add_command(
        _pending(
            "load",
            "Load a task into a persistent task session.",
            "공통 task session 생성·로딩 서비스와 화면 연결이 필요합니다.",
            click.Argument(["task_id"]),
        )
    )
    sim.add_command(
        _pending(
            "validate",
            "Validate a simulation against physical evidence.",
            "보류 실험 데이터와 simulation validation 서비스 연결이 필요합니다.",
            setup_option(),
        )
    )
    sim.add_command(
        _pending(
            "reset",
            "Execute the setup's physical reset protocol.",
            "선택된 task의 물리 reset 프로토콜·실행기·복구 관측 연결이 필요합니다.",
            setup_option(),
        )
    )
    cli.add_command(
        _group(
            "assets",
            _pending(
                "search",
                "Search and resolve candidate simulation assets.",
                "에셋 검색 provider와 revision 고정 resolver 연결이 필요합니다.",
                click.Argument(["query"]),
            ),
            _pending(
                "inspect",
                "Inspect an asset's provenance and physics metadata.",
                "에셋 manifest·의존성·라이선스·물리 metadata 검사기 연결이 필요합니다.",
                click.Argument(["asset"]),
            ),
        )
    )
    cli.add_command(
        _group(
            "experiments",
            _pending(
                "list",
                "List compatible experiments and observation requirements.",
                "task별 실험 registry와 관측 producer 요구사항 서비스 연결이 필요합니다.",
                task_option(),
            ),
            _pending(
                "run",
                "Execute a bounded physical experiment.",
                "bounded experiment 실행기와 Reality evidence producer 연결이 필요합니다.",
                click.Argument(["experiment_id"]),
                task_option(),
            ),
        )
    )
    cli.add_command(
        _group(
            "sysid",
            _pending(
                "fit",
                "Fit an evidence-backed physical parameter posterior.",
                "dataset 해석·품질 검사와 등록된 SysID estimator 서비스 연결이 필요합니다.",
                task_option(),
                click.Option(["--dataset"], required=True),
            ),
        )
    )
    cli.add_command(
        _group(
            "models",
            _pending(
                "list",
                "List compatible physics model hypotheses.",
                "물리 hypothesis registry와 solver·관측 compatibility 서비스 연결이 필요합니다.",
                task_option(),
            ),
            _pending(
                "select",
                "Select or reject models using held-out evidence.",
                "모델 후보 registry와 보류 데이터 기반 selection 서비스 연결이 필요합니다.",
                task_option(),
                click.Option(["--dataset"], required=True),
            ),
        )
    )
    cli.add_command(
        _group(
            "policy",
            _pending(
                "plan",
                "Plan candidate actions in a learned Twin.",
                "learned Twin의 action 후보 생성·rollout 서비스 연결이 필요합니다.",
                task_option(),
                setup_option(),
            ),
            _pending(
                "validate",
                "Validate a candidate policy before deployment.",
                "policy artifact 해석과 독립 Twin validation 서비스 연결이 필요합니다.",
                click.Option(["--policy"], required=True),
            ),
        )
    )
    cli.add_command(
        _group(
            "runs",
            _pending(
                "status",
                "Inspect an existing run.",
                "지속 실행 중인 RunManager에 접근하는 run 조회 client 연결이 필요합니다.",
                click.Argument(["run_id"]),
            ),
            _pending(
                "events",
                "Read committed events from a run.",
                "committed event cursor를 읽는 run event client 연결이 필요합니다.",
                click.Argument(["run_id"]),
            ),
            _pending(
                "stop",
                "Request a bounded safe stop of an active run.",
                "RunManager와 안전 중단·종료 확인 client 연결이 필요합니다.",
                click.Argument(["run_id"]),
            ),
        )
    )
    cli.add_command(
        _group(
            "artifacts",
            _pending(
                "inspect",
                "Inspect an artifact within its visibility boundary.",
                "artifact resolver와 visibility 경계를 지키는 조회 서비스 연결이 필요합니다.",
                click.Argument(["artifact"]),
            ),
        )
    )
    cli.add_command(
        _pending(
            "replay",
            "Replay committed world states and evidence.",
            "CLI replay adapter와 기록된 run·event·world state resolver 연결이 필요합니다.",
            click.Option(["--run"], required=True),
        )
    )
    cli.add_command(
        _pending(
            "doctor",
            "Check runtime, device and required producers.",
            "runtime·device·sensor producer별 진단 서비스 연결이 필요합니다.",
            device_option(),
        )
    )
    cli.add_command(
        _group(
            "hardware",
            _pending(
                "preflight",
                "Check hardware readiness for a deployment bundle.",
                "현장 calibration·readiness probe와 hardware preflight 서비스 연결이 필요합니다.",
                bundle_option(),
            ),
            _pending(
                "deploy",
                "Deploy an admitted bundle through the hardware executor.",
                "검증된 deployment bundle·native executor·실제 hardware transport 연결이 필요합니다.",
                bundle_option(),
            ),
        )
    )
