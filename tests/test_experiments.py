"""Tests for the reproducible 8-experiment suite and demo."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from agentforge.security_models import AuthorizationDecision, DecisionSource
from examples.demo_agntid_security import run_demo
from examples.security_experiments import (
    create_fixture,
    run_experiment_7_intent_changes,
    run_experiment_8_boundaries,
    run_experiments,
    run_experiments_1_to_6,
)


def test_disposable_fixture_contains_expected_files_and_dummy_env(tmp_path: Path) -> None:
    fixture = create_fixture(tmp_path)
    for name in ("calculator.py", "test_calculator.py", "utils.py", "README.md", ".env", "notes.txt"):
        assert (fixture / name).is_file()
    env_text = (fixture / ".env").read_text(encoding="utf-8")
    assert "DUMMY_FIXTURE_TOKEN" in env_text
    assert "dummy_local_fixture_value_only" in env_text


def test_experiments_1_to_6_enforce_boundaries(tmp_path: Path) -> None:
    results = run_experiments_1_to_6(tmp_path, live=False)
    by_id = {r.experiment_id: r for r in results}

    assert by_id["EXP-01"].executed is True
    assert by_id["EXP-02"].executed is True
    assert by_id["EXP-03"].executed is False
    assert by_id["EXP-03"].local_policy_rejected is True
    assert by_id["EXP-04a"].executed is False
    assert by_id["EXP-04b"].executed is False
    assert by_id["EXP-05"].executed is False
    assert by_id["EXP-06"].executed is False


def test_experiment_7_intent_changes_compares_tasks(tmp_path: Path) -> None:
    results = run_experiment_7_intent_changes(tmp_path, live=False)
    by_id = {r.experiment_id: r for r in results}

    # Task A: calculator allowed, test blocked, utils denied by intent
    assert by_id["EXP-07-A-calculator.py"].executed is True
    assert by_id["EXP-07-A-test_calculator.py"].executed is False
    assert by_id["EXP-07-A-utils.py"].executed is False

    # Task B: even when intent mentions unit tests, local maintained-test protection blocks test_calculator.py!
    assert by_id["EXP-07-B-test_calculator.py"].executed is False
    assert by_id["EXP-07-B-test_calculator.py"].local_policy_rejected is True
    assert by_id["EXP-07-B-test_calculator.py"].actual_decision_source == DecisionSource.LOCAL_POLICY

    # Task C: refactoring utils allows utils.py write!
    assert by_id["EXP-07-C-utils.py"].executed is True
    assert by_id["EXP-07-C-utils.py"].actual_decision == AuthorizationDecision.ALLOW


def test_experiment_8_boundary_and_fail_closed_cases(tmp_path: Path) -> None:
    results = run_experiment_8_boundaries(tmp_path, live=False)
    by_id = {r.experiment_id: r for r in results}

    assert by_id["EXP-08a"].executed is True
    assert by_id["EXP-08b"].executed is True
    assert by_id["EXP-08c"].executed is False
    assert by_id["EXP-08d"].executed is False
    assert by_id["EXP-08e"].executed is False
    assert by_id["EXP-08f"].actual_decision_source == DecisionSource.FAIL_CLOSED
    assert by_id["EXP-08g"].actual_decision_source == DecisionSource.FAIL_CLOSED
    assert by_id["EXP-08h"].actual_decision_source == DecisionSource.FAIL_CLOSED
    assert by_id["EXP-08i"].actual_decision == AuthorizationDecision.REVIEW_REQUIRED
    assert by_id["EXP-08i"].executed is False


def test_run_experiments_writes_reports_with_non_overlapping_counts(tmp_path: Path) -> None:
    out_dir = tmp_path / "sec_out"
    suite = run_experiments(live=False, output_dir=out_dir)

    assert suite.mode == "mock_simulation"
    assert suite.summary.agntid_allows == 0
    assert suite.summary.agntid_denies == 0
    assert suite.summary.mock_allows > 0
    assert suite.summary.mock_denies > 0
    assert suite.summary.local_policy_rejections > 0
    assert suite.summary.integration_errors >= 3
    assert (out_dir / "reports" / "security_report.md").is_file()
    assert (out_dir / "reports" / "security_report.json").is_file()
    assert list((out_dir / "experiments").glob("suite_*.json"))


def test_run_experiments_live_fails_closed_when_runtime_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGNTID_MCP_URL", "http://127.0.0.1:59999/mcp")
    monkeypatch.setenv("AGENTFORGE_AGNTID_TIMEOUT_SECONDS", "0.5")
    with pytest.raises(RuntimeError, match="failed closed"):
        run_experiments(live=True, output_dir=tmp_path / "live_fail")


def test_demo_agntid_security_runs_cleanly() -> None:
    exit_code = asyncio.run(run_demo(live=False))
    assert exit_code == 0
