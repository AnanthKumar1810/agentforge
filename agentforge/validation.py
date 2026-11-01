"""Deterministic syntax/test checks with bounded execution time."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from agentforge.models import ValidationCheck, ValidationSummary

MAX_OUTPUT_CHARS = 12_000


def _safe_env(workspace: Path) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({"PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
    return env


def _run_check(name: str, command: list[str], cwd: Path, timeout_seconds: int, *, container_name: str | None = None) -> ValidationCheck:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=cwd, env=_safe_env(cwd), shell=False, timeout=timeout_seconds,
            capture_output=True, text=True, errors="replace", check=False,
        )
        output = (proc.stdout + "\n" + proc.stderr).strip()[-MAX_OUTPUT_CHARS:]
        return ValidationCheck(
            name=name, passed=proc.returncode == 0,
            duration_seconds=round(time.monotonic() - started, 3),
            exit_code=proc.returncode, output=output,
        )
    except subprocess.TimeoutExpired as exc:
        if container_name:
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        detail = exc.stdout or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        return ValidationCheck(
            name=name, passed=False, duration_seconds=round(time.monotonic() - started, 3),
            exit_code=None, output=(str(detail) + f"\nTimed out after {timeout_seconds}s")[-MAX_OUTPUT_CHARS:],
        )
    except OSError as exc:
        return ValidationCheck(
            name=name, passed=False, duration_seconds=round(time.monotonic() - started, 3),
            exit_code=None, output=f"Could not start check: {exc}",
        )


def _docker_command(root: Path, arguments: list[str]) -> tuple[list[str], str]:
    name = "agentforge-validation-" + uuid.uuid4().hex[:16]
    return ([
        "docker", "run", "--rm", "--pull=never", "--name", name,
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=512m", "--cpus=1",
        "--user=65534:65534", "--tmpfs=/tmp:rw,nosuid,size=64m",
        "--mount", f"type=bind,source={root},target=/workspace,readonly",
        "--workdir=/workspace", "--env=PYTHONPATH=/workspace:/workspace/src",
        "--env=PYTHONDONTWRITEBYTECODE=1", "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "agentforge-validator:py313", "python", *arguments,
    ], name)


def validate_workspace(workspace: Path, *, timeout_seconds: int = 90,
                       selected_tests: list[str] | None = None, runner: str = "local") -> ValidationSummary:
    if runner not in {"local", "docker"}:
        raise ValueError("runner must be local or docker")
    root = workspace.resolve()
    started = time.monotonic()
    # Parse source without importing it or writing bytecode.
    syntax = ["-c", "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in pathlib.Path('.').rglob('*.py')]"]
    if runner == "docker":
        command, container = _docker_command(root, syntax)
        checks = [_run_check("python-syntax", command, root, timeout_seconds, container_name=container)]
    else:
        checks = [_run_check("python-syntax", [sys.executable, *syntax], root, timeout_seconds)]
    tests_exist = any(root.rglob("test_*.py")) or any(root.rglob("*_test.py"))
    issues: list[str] = []
    if tests_exist:
        targets = selected_tests or ["."]
        if runner == "docker":
            paths = ["/workspace/" + path if path != "." else "/workspace" for path in targets]
            command, container = _docker_command(root, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest", *paths])
            checks.append(_run_check("pytest", command, root, timeout_seconds, container_name=container))
        else:
            checks.append(_run_check("pytest", [sys.executable, "-m", "pytest", "-q", *targets], root, timeout_seconds))
        if selected_tests:
            if runner == "docker":
                command, container = _docker_command(root, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest", "/workspace"])
                checks.append(_run_check("pytest-full", command, root, timeout_seconds, container_name=container))
            else:
                checks.append(_run_check("pytest-full", [sys.executable, "-m", "pytest", "-q"], root, timeout_seconds))
        if checks[-1].exit_code == 5:
            issues.append("pytest found no tests to run")
    else:
        issues.append("No test files found; pytest was skipped")
    issues.extend(
        f"{check.name} failed (exit={check.exit_code}): {check.output[-1800:]}"
        for check in checks if not check.passed
    )
    return ValidationSummary(
        passed=tests_exist and all(check.passed for check in checks), checks=checks, issues=issues,
        status=("verified" if tests_exist and all(check.passed for check in checks) else "syntax_only" if not tests_exist and all(check.passed for check in checks) else "failed"),
        selected_tests=targets if tests_exist else [],
        duration_seconds=round(time.monotonic() - started, 3),
    )
