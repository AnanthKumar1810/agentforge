"""Tests for structured security reporting and secret non-leakage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from agentforge.models import RunReport, ValidationSummary
from agentforge.reporting import write_reports
from agentforge.security_models import (
    AuthorizationDecision,
    AuthorizationRecord,
    DecisionSource,
    SecuritySummary,
)
from agentforge.security_reporting import write_security_report


def test_security_report_distinguishes_all_sources(tmp_path: Path) -> None:
    summary = SecuritySummary(
        intended_task="Fix calculator.py",
        integration_status="mock_simulation",
        task_id="task-123",
        agent_id="agentforge:v1",
        user_id="tester",
        eligible_tools=["read_file", "write_file"],
        shortlisted_tools=["read_file", "write_file"],
    )
    summary.add_record(
        AuthorizationRecord(
            tool_name="read_file",
            target_resource="calculator.py",
            normalized_path="calculator.py",
            decision=AuthorizationDecision.ALLOW,
            decision_source=DecisionSource.AGNTID,
            agntid_verdict="allow",
            agntid_decision_source="agntid",
            executed=True,
            explanation="Allowed by live AgntID",
        )
    )
    summary.add_record(
        AuthorizationRecord(
            tool_name="write_file",
            target_resource="utils.py",
            normalized_path="utils.py",
            decision=AuthorizationDecision.DENY,
            decision_source=DecisionSource.MOCK,
            agntid_verdict="deny",
            agntid_decision_source="mock",
            executed=False,
            explanation="Denied by mock simulation",
        )
    )
    summary.add_record(
        AuthorizationRecord(
            tool_name="read_file",
            target_resource=".env",
            normalized_path=".env",
            decision=AuthorizationDecision.DENY,
            decision_source=DecisionSource.LOCAL_POLICY,
            agntid_verdict="allow",
            agntid_decision_source="agntid",
            local_policy_rejected=True,
            local_policy_reason="Credential path cannot be read",
            executed=False,
            explanation="Rejected by AgentForge local policy",
        )
    )
    summary.add_record(
        AuthorizationRecord(
            tool_name="write_file",
            target_resource="calculator.py",
            normalized_path="calculator.py",
            decision=AuthorizationDecision.ERROR,
            decision_source=DecisionSource.FAIL_CLOSED,
            executed=False,
            explanation="Timeout fail-closed",
            error="TimeoutError",
        )
    )

    artifacts = write_security_report(summary, tmp_path / "sec")
    md_text = Path(artifacts["security_markdown"]).read_text(encoding="utf-8")
    json_data = json.loads(Path(artifacts["security_json"]).read_text(encoding="utf-8"))

    assert json_data["agntid_allows"] == 2  # Two records had agntid allow (one overridden by local_policy!)
    assert json_data["mock_denies"] == 1
    assert json_data["local_policy_rejections"] == 1
    assert json_data["integration_errors"] == 1
    assert json_data["executed_actions"] == 1
    assert json_data["prevented_actions"] == 3
    assert "OFFLINE SIMULATION NOTICE" in md_text
    assert "Decision Source Legend" in md_text
    assert "local_policy" in md_text
    assert "fail_closed" in md_text


def test_write_reports_embeds_security_and_writes_artifacts(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    sec = SecuritySummary(intended_task="Fix calculator.py", integration_status="mock_simulation")
    sec.add_record(
        AuthorizationRecord(
            tool_name="read_file",
            target_resource="calculator.py",
            normalized_path="calculator.py",
            decision=AuthorizationDecision.ALLOW,
            decision_source=DecisionSource.MOCK,
            agntid_verdict="allow",
            agntid_decision_source="mock",
            executed=True,
        )
    )
    report = RunReport(
        run_id="c" * 32,
        request="Fix calculator.py",
        status="passed",
        started_at=now,
        finished_at=now,
        workspace=str(tmp_path),
        validation=ValidationSummary(passed=True, status="verified"),
        security=sec,
    )
    artifacts = write_reports(report, tmp_path / "artifacts")
    assert "security_json" in artifacts
    assert "security_markdown" in artifacts
    assert Path(artifacts["security_json"]).is_file()
    assert Path(artifacts["security_markdown"]).is_file()

    main_md = Path(artifacts["markdown"]).read_text(encoding="utf-8")
    assert "AgentForge Security & AgntID Authorization Report" in main_md
    saved_json = json.loads(Path(artifacts["json"]).read_text(encoding="utf-8"))
    assert saved_json["artifacts"] == artifacts
    assert saved_json["security"]["mock_allows"] == 1
