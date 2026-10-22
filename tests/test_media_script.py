"""Contract tests for the Agent Lab media wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "record_agent_lab_demo.sh"


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_media_script_help_documents_capture_contract() -> None:
    result = run_script("--help")

    assert result.returncode == 0
    assert "fixed clock" in result.stdout
    assert "blank WebGL viewport" in result.stdout
    assert "--run DIR" in result.stdout
    assert "--expect-task ID" in result.stdout


def test_media_script_rejects_invalid_dimensions_before_capture() -> None:
    result = run_script("--width", "0")

    assert result.returncode != 0
    assert "--width must be a positive integer" in result.stderr


def test_media_script_requires_a_committed_event_log(tmp_path: Path) -> None:
    result = run_script("--run", str(tmp_path))

    assert result.returncode != 0
    assert "events.ndjson" in result.stderr


def test_media_script_rejects_a_run_for_the_wrong_task(tmp_path: Path) -> None:
    (tmp_path / "events.ndjson").write_text(
        '{"type":"run.started","payload":{"task_id":"push-cube-v1"}}\n'
    )
    result = run_script("--run", str(tmp_path), "--expect-task", "push-t-bar-v1")

    assert result.returncode != 0
    assert "does not match --expect-task 'push-t-bar-v1'" in result.stderr
