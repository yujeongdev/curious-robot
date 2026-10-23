from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from curious_robot.curo_cli import build_cli, main
from curious_robot.curo_cli.output import diagnostics_to_stderr
from curious_robot.curo_cli.registry import iter_commands


def test_catalog_and_capability_discovery_do_not_load_gpu_libraries() -> None:
    script = """
import json
import sys
from curious_robot.curo_cli import build_cli, main
main(['capabilities', '--json'])
assert not {'newton', 'warp', 'matplotlib', 'curious_robot.environment'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, check=True)
    document = json.loads(result.stdout)
    commands = {item["command"]: item for item in document["commands"]}
    assert commands["curo sim setup"]["status"] == "implemented"
    assert commands["curo hardware deploy"]["status"] == "not_implemented"
    assert "hardware transport" in commands["curo hardware deploy"]["implementation_hint"]
    assert document["exit_codes"]["not_implemented"] == 3


def test_task_catalog_discovery_does_not_load_gpu_libraries() -> None:
    script = """
import sys
from curious_robot.curo_cli import build_cli, main
main(['tasks', 'list', '--json'])
assert not {'newton', 'warp', 'matplotlib', 'curious_robot.environment'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["status"] == "passed"


def test_tasks_come_from_registered_presets(capsys) -> None:  # noqa: ANN001
    main(["tasks", "list", "--json"])
    document = json.loads(capsys.readouterr().out)
    tasks = {item["task_id"]: item for item in document["tasks"]}
    assert set(tasks) == {"push-cube-v1", "push-t-bar-v1"}
    assert tasks["push-t-bar-v1"]["requires_yaw"] is True
    assert tasks["push-cube-v1"]["requires_yaw"] is False
    assert tasks["push-t-bar-v1"]["object_archetype"]["archetype_id"] == "t-bar"


def test_unknown_task_fails_without_runtime_import(capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit) as error:
        main(["tasks", "show", "does-not-exist", "--json"])
    assert error.value.code == 2
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "failed"
    assert document["error"]["type"] == "KeyError"


def test_run_requires_an_explicit_task() -> None:
    with pytest.raises(SystemExit) as error:
        main(["run"])
    assert error.value.code == 2


def test_run_defaults_to_isolated_pipeline(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    calls = []

    def run_slice(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        print("runtime diagnostic")
        output = kwargs["output_directory"]
        output.mkdir(exist_ok=True)
        (output / "friction_capability_manifest.json").write_text(json.dumps({"status": "PASSED"}))

    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=run_slice)
    )
    output = tmp_path / "isolated"
    main(["run", "--task", "push-t-bar-v1", "--output", str(output)])
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["pipeline"] == "isolated"
    assert document["task_id"] == "push-t-bar-v1"
    assert calls[0]["task_id"] == "push-t-bar-v1"
    assert "runtime diagnostic" in captured.err


def test_explicit_legacy_pipeline_reports_failure(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    runner = Mock(
        return_value=SimpleNamespace(success=False, model_dump=lambda **_kwargs: {"success": False})
    )
    monkeypatch.setitem(sys.modules, "curious_robot.runner", SimpleNamespace(run_episode=runner))
    with pytest.raises(SystemExit) as error:
        main(
            [
                "run",
                "--task",
                "push-cube-v1",
                "--pipeline",
                "legacy",
                "--no-robot",
                "--output",
                str(tmp_path / "legacy"),
            ]
        )
    assert error.value.code == 2
    document = json.loads(capsys.readouterr().out)
    assert document["pipeline"] == "legacy"
    assert document["status"] == "failed"
    assert runner.call_args.kwargs["include_robot"] is False


def test_reduced_scene_cannot_silently_replace_isolated_pipeline(capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--no-robot"])
    assert error.value.code == 2
    assert "requires --pipeline legacy" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_unexpected_runtime_error_has_exit_one_and_pipeline_context(monkeypatch, capsys) -> None:  # noqa: ANN001
    runner = Mock(side_effect=RuntimeError("worker unexpectedly disconnected"))
    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=runner)
    )
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1"])
    assert error.value.code == 1
    document = json.loads(capsys.readouterr().out)
    assert document["error"]["code"] == "runtime_error"
    assert document["task_id"] == "push-cube-v1"
    assert document["pipeline"] == "isolated"


def test_failed_isolated_run_keeps_auto_output_and_persisted_manifest(
    tmp_path,
    monkeypatch,
    capsys,  # noqa: ANN001
) -> None:
    monkeypatch.chdir(tmp_path)

    def failed_runner(**kwargs):  # noqa: ANN003, ANN202
        output = kwargs["output_directory"]
        output.mkdir(parents=True, exist_ok=True)
        (output / "friction_capability_manifest.json").write_text(
            json.dumps({"status": "FAILED", "failure_artifact": "sha256:failure-evidence"})
        )
        raise RuntimeError("worker failed after recording evidence")

    monkeypatch.setitem(
        sys.modules,
        "curious_robot.lab.friction_runner",
        SimpleNamespace(run_newton_friction_slice=failed_runner),
    )
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-t-bar-v1", "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    output = Path(report["output_directory"])
    manifest = Path(report["failure_manifest"])
    assert output.is_absolute()
    assert output.parent == tmp_path / "artifacts" / "curo" / "run"
    assert manifest == output / "friction_capability_manifest.json"
    assert json.loads(manifest.read_text())["failure_artifact"] == "sha256:failure-evidence"
    assert report["task_id"] == "push-t-bar-v1"
    assert report["pipeline"] == "isolated"


def test_isolated_quality_gate_failure_is_a_failed_result_with_exit_two(
    tmp_path,
    monkeypatch,
    capsys,  # noqa: ANN001
) -> None:
    from curious_robot.lab.friction_artifacts import CapabilityExecutionError

    monkeypatch.chdir(tmp_path)

    def rejected_runner(**kwargs):  # noqa: ANN003, ANN202
        output = kwargs["output_directory"]
        output.mkdir(parents=True, exist_ok=True)
        (output / "friction_capability_manifest.json").write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "failure_type": "CapabilityExecutionError",
                    "failure_artifact": "sha256:quality-evidence",
                }
            )
        )
        raise CapabilityExecutionError("trajectory quality gate rejected excessive_yaw_motion")

    monkeypatch.setitem(
        sys.modules,
        "curious_robot.lab.friction_runner",
        SimpleNamespace(run_newton_friction_slice=rejected_runner),
    )
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--json"])
    assert error.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["error"]["code"] == "validation_failed"
    assert report["error"]["type"] == "CapabilityExecutionError"
    assert "excessive_yaw_motion" in report["error"]["message"]
    assert report["result"]["failure_artifact"] == "sha256:quality-evidence"
    assert (
        Path(report["failure_manifest"])
        == Path(report["output_directory"]) / "friction_capability_manifest.json"
    )
    assert report["task_id"] == "push-cube-v1"
    assert report["pipeline"] == "isolated"


@pytest.mark.parametrize("pipeline", ["isolated", "legacy"])
def test_run_rejects_nonempty_output_before_invoking_either_pipeline(
    tmp_path,
    monkeypatch,
    capsys,
    pipeline,  # noqa: ANN001
) -> None:
    output = tmp_path / "existing-run"
    output.mkdir(exist_ok=True)
    retained = output / "friction_capability_manifest.json"
    retained.write_text('{"status":"PASSED","retained":true}')
    isolated = Mock()
    legacy = Mock()
    monkeypatch.setitem(
        sys.modules,
        "curious_robot.lab.friction_runner",
        SimpleNamespace(run_newton_friction_slice=isolated),
    )
    monkeypatch.setitem(sys.modules, "curious_robot.runner", SimpleNamespace(run_episode=legacy))
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--pipeline", pipeline, "--output", str(output)])
    assert error.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["output_directory"] == str(output)
    assert report["task_id"] == "push-cube-v1"
    assert report["pipeline"] == pipeline
    assert report["error"]["type"] == "FileExistsError"
    assert "failure_manifest" not in report
    assert json.loads(retained.read_text()) == {"status": "PASSED", "retained": True}
    isolated.assert_not_called()
    legacy.assert_not_called()


def test_run_accepts_existing_empty_output_directory(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    output = tmp_path / "empty"
    output.mkdir(exist_ok=True)

    def run_slice(**kwargs):  # noqa: ANN003, ANN202
        (kwargs["output_directory"] / "friction_capability_manifest.json").write_text(
            json.dumps({"status": "PASSED"})
        )

    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=run_slice)
    )
    main(["run", "--task", "push-cube-v1", "--output", str(output)])
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_every_pending_command_is_a_parseable_non_success() -> None:
    runner = CliRunner()
    cli = build_cli()
    for path, command in iter_commands(cli):
        if command.implemented:
            continue
        arguments = list(path)
        for parameter in command.params:
            if isinstance(parameter, click.Argument):
                arguments.append("example")
            elif parameter.required:
                arguments.extend((parameter.opts[0], "example"))
        arguments.append("--json")
        result = runner.invoke(cli, arguments)
        assert result.exit_code == 3, path
        document = json.loads(result.stdout)
        assert document["status"] == "not_implemented", path
        assert document["error"]["code"] == "not_implemented"
        assert document["message"] == "아직 구현되지 않았습니다"
        assert command.implementation_hint in document["hint"]


def test_every_command_help_loads_without_a_physics_backend() -> None:
    runner = CliRunner()
    cli = build_cli()
    for path, _command in iter_commands(cli):
        result = runner.invoke(cli, [*path, "--help"])
        assert result.exit_code == 0, (path, result.output)
        assert "--json" in result.output


def test_click_validates_native_choice_before_loading_pipeline() -> None:
    result = CliRunner().invoke(build_cli(), ["run", "--task", "push-cube-v1", "--pipeline", "unknown"])
    assert result.exit_code == 2
    assert "Invalid value for '--pipeline'" in result.output


def test_concurrent_runs_cannot_claim_the_same_empty_directory(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from curious_robot.curo_cli.commands.runs import _reserve_run_directory

    barrier = Barrier(2)
    directory = tmp_path / "shared"

    def reserve() -> bool:
        barrier.wait(timeout=5)
        try:
            _reserve_run_directory(directory, task="push-cube-v1", pipeline="isolated")
        except FileExistsError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _index: reserve(), range(2))) == [False, True]
    assert json.loads((directory / ".curo-run.json").read_text()) == {
        "task_id": "push-cube-v1",
        "pipeline": "isolated",
    }


def test_pending_command_has_a_human_korean_message(capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit):
        main(["hardware", "deploy", "--bundle", "does-not-exist.json"])
    output = capsys.readouterr().out
    assert "아직 구현되지 않았습니다" in output
    assert "작업을 수행하지 않았습니다" in output


def test_native_and_child_diagnostics_are_separated_from_json(capfd) -> None:  # noqa: ANN001
    with diagnostics_to_stderr():
        print("python diagnostic")
        os.write(1, b"native diagnostic\n")
        subprocess.run([sys.executable, "-c", "print('child diagnostic')"], check=True)
    print('{"status":"passed"}')
    captured = capfd.readouterr()
    assert json.loads(captured.out) == {"status": "passed"}
    assert all(
        label in captured.err for label in ("python diagnostic", "native diagnostic", "child diagnostic")
    )


def test_legacy_web_loads_task_without_autorun(monkeypatch) -> None:  # noqa: ANN001
    from curious_robot.cli import main as legacy_main

    urls = []
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=Mock()))
    monkeypatch.setitem(sys.modules, "curious_robot.web_api", SimpleNamespace(create_app=lambda: object()))
    monkeypatch.setattr(
        "curious_robot.cli.threading.Timer",
        lambda _delay, _callable, args: SimpleNamespace(start=lambda: urls.append(args[0])),
    )
    legacy_main(["web", "--task", "push-t-bar-v1", "--seed", "9", "--device", "cuda:0"])
    assert "task=push-t-bar-v1" in urls[0]
    assert "seed=9" in urls[0]
    assert "device=cuda%3A0" in urls[0]
    assert "autorun" not in urls[0]


def test_web_facade_preserves_explicit_task_and_autorun(monkeypatch, capsys) -> None:  # noqa: ANN001
    from curious_robot import cli

    legacy_main = Mock()
    monkeypatch.setattr(cli, "main", legacy_main)
    main(["web", "--task", "push-t-bar-v1", "--no-open", "--autorun"])
    arguments = legacy_main.call_args.args[0]
    assert arguments[arguments.index("--task") + 1] == "push-t-bar-v1"
    assert "--no-open" in arguments
    assert "--autorun" in arguments
    assert json.loads(capsys.readouterr().out)["task_id"] == "push-t-bar-v1"


def test_web_missing_optional_dependency_returns_json(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    with pytest.raises(SystemExit) as error:
        main(["web", "--task", "push-cube-v1", "--no-open", "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["task_id"] == "push-cube-v1"
    assert "Install web dependencies" in report["error"]["message"]


def test_web_host_validation_returns_json(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["web", "--task", "push-cube-v1", "--host", "0.0.0.0", "--no-open", "--json"])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "command_failed"
    assert "non-loopback hosting" in captured.err


def test_web_server_startup_exit_returns_json(monkeypatch, capsys) -> None:
    from curious_robot import cli

    monkeypatch.setattr(cli, "main", Mock(side_effect=SystemExit(1)))
    with pytest.raises(SystemExit) as error:
        main(["web", "--task", "push-cube-v1", "--no-open", "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"]["code"] == "runtime_error"
    assert "during startup" in report["error"]["message"]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX stdio boundary")
def test_buffered_native_diagnostics_cannot_contaminate_json() -> None:
    script = """
import ctypes
from curious_robot.curo_cli.output import diagnostics_to_stderr
with diagnostics_to_stderr():
    runtime = ctypes.CDLL(None)
    runtime.printf(b"native buffered diagnostic\\n")
print('{"status":"passed"}')
"""
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"status": "passed"}
    assert "native buffered diagnostic" in result.stderr


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("backend timed out"),
        ConnectionResetError("worker connection reset"),
        KeyError("solver state disappeared"),
        ValueError("solver returned an invalid internal state"),
    ],
)
def test_backend_errors_are_runtime_failures_not_input_errors(monkeypatch, capsys, tmp_path, failure) -> None:
    runner = Mock(side_effect=failure)
    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=runner)
    )
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--output", str(tmp_path / "run"), "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"]["code"] == "runtime_error"
    assert report["error"]["type"] == type(failure).__name__
    assert report["task_id"] == "push-cube-v1"
    assert report["pipeline"] == "isolated"
    assert report["output_directory"] == str(tmp_path / "run")


def test_nonfinite_manifest_returns_one_runtime_json_with_evidence_location(
    tmp_path, monkeypatch, capsys
) -> None:
    def run_slice(**kwargs):
        (kwargs["output_directory"] / "friction_capability_manifest.json").write_text(
            '{"status":"PASSED","residual":NaN}'
        )

    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=run_slice)
    )
    output = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-t-bar-v1", "--output", str(output), "--json"])
    assert error.value.code == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "failed"
    assert report["error"]["code"] == "runtime_error"
    assert report["error"]["type"] == "ValueError"
    assert report["task_id"] == "push-t-bar-v1"
    assert report["pipeline"] == "isolated"
    assert report["output_directory"] == str(output)
    assert report["manifest_path"] == str(output / "friction_capability_manifest.json")
    assert "result" not in report
    assert "Traceback" not in captured.err


def test_cutoff_manifest_is_a_runtime_error_with_retained_evidence(tmp_path, monkeypatch, capsys) -> None:
    def run_slice(**kwargs):
        (kwargs["output_directory"] / "friction_capability_manifest.json").write_text('{"status":')

    monkeypatch.setitem(
        sys.modules, "curious_robot.lab.friction_runner", SimpleNamespace(run_newton_friction_slice=run_slice)
    )
    output = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--output", str(output), "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"]["code"] == "runtime_error"
    assert report["error"]["type"] == "JSONDecodeError"
    assert report["output_directory"] == str(output)
    assert report["failure_manifest"] == str(output / "friction_capability_manifest.json")


def test_unserializable_legacy_result_returns_minimal_runtime_json(tmp_path, monkeypatch, capsys) -> None:
    runner = Mock(
        return_value=SimpleNamespace(success=True, model_dump=lambda **_kwargs: {"invalid": {1, 2}})
    )
    monkeypatch.setitem(sys.modules, "curious_robot.runner", SimpleNamespace(run_episode=runner))
    output = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        main(["run", "--task", "push-cube-v1", "--pipeline", "legacy", "--output", str(output), "--json"])
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"]["code"] == "runtime_error"
    assert report["error"]["type"] == "TypeError"
    assert report["output_directory"] == str(output)
    assert "result" not in report
