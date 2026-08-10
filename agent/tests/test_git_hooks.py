from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".githooks" / "pre-push"


def _write_command(path: Path, name: str, body: str) -> None:
    command = path / name
    command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    command.chmod(0o755)


def test_pre_push_checks_run_from_dashboard_directory(tmp_path):
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    log_path = tmp_path / "commands.log"
    recorder = 'printf "%s|%s\\n" "$PWD" "$*" >> "$HOOK_TEST_LOG"'
    _write_command(command_dir, "npx", recorder)
    _write_command(command_dir, "npm", recorder)

    env = os.environ.copy()
    env["PATH"] = f"{command_dir}:{env['PATH']}"
    env["HOOK_TEST_LOG"] = str(log_path)

    result = subprocess.run(
        [str(HOOK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    commands = log_path.read_text(encoding="utf-8").splitlines()
    dashboard = str(ROOT / "dashboard")
    assert commands == [
        f"{dashboard}|tsc --noEmit",
        f"{dashboard}|test",
        f"{dashboard}|run build",
    ]


def test_pre_push_blocks_when_dashboard_check_fails(tmp_path):
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    _write_command(command_dir, "npx", "exit 0")
    _write_command(
        command_dir,
        "npm",
        'if [ "$1 $2" = "run build" ]; then exit 23; fi\nexit 0',
    )

    env = os.environ.copy()
    env["PATH"] = f"{command_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(HOOK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
