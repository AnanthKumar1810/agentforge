"""Deterministic syntax/test checks with bounded execution time."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from agentforge.models import ValidationCheck, ValidationSummary

MAX_OUTPUT_CHARS = 12_000


def _safe_env(workspace: Path) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({"PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})
    return env


def _run_check(name: str, command: list[str], cwd: Path, timeout_seconds: int) -> ValidationCheck:
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


def validate_workspace(workspace: Path, *, timeout_seconds: int = 90) -> ValidationSummary:
    root = workspace.resolve()
    started = time.monotonic()
    checks = [_run_check("python-compileall", [sys.executable, "-m", "compileall", "-q", "."], root, timeout_seconds)]
    tests_exist = any(root.rglob("test_*.py")) or any(root.rglob("*_test.py"))
    issues: list[str] = []
    if tests_exist:
        checks.append(_run_check("pytest", [sys.executable, "-m", "pytest", "-q"], root, timeout_seconds))
        if checks[-1].exit_code == 5:
            issues.append("pytest found no tests to run")
    else:
        issues.append("No test files found; pytest was skipped")
    issues.extend(
        f"{check.name} failed (exit={check.exit_code}): {check.output[-1800:]}"
        for check in checks if not check.passed
    )
    return ValidationSummary(
        passed=all(check.passed for check in checks), checks=checks, issues=issues,
        duration_seconds=round(time.monotonic() - started, 3),
    )
