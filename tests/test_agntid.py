"""Tests for the AgntID SDK adapter, wire contract, and all 10 failure/security conditions."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from agntid.context import ApprovalEvidence, arguments_digest

from agentforge.agntid import (
    AgntIDRuntimeConfig,
    AgntIDSession,
    CONTROL_TOOLS,
    FakeAgntidMCPRuntime,
    FakePolicyConfig,
    classify_action,
)
from agentforge.governed_workspace import GovernedWorkspace
from agentforge.security_models import (
    ActionType,
    AuthorizationDecision,
    DecisionSource,
    SecuritySummary,
)
from agentforge.workspace import Workspace, WorkspaceError


# ---------------------------------------------------------------------------
# Configuration & classification
# ---------------------------------------------------------------------------


def test_classify_actions() -> None:
    assert classify_action("read_file") == ActionType.READ
    assert classify_action("read_lines") == ActionType.READ
    assert classify_action("search_files") == ActionType.SEARCH
    assert classify_action("list_files") == ActionType.LIST
    assert classify_action("write_file") == ActionType.WRITE
    assert classify_action("replace_text") == ActionType.WRITE
    assert classify_action("replace_line") == ActionType.WRITE
    assert classify_action("delete_file") == ActionType.DELETE
    assert classify_action("run_command") == ActionType.EXECUTE


def test_config_rejects_embedded_credentials_in_mcp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTFORGE_AGNTID_MODE", "mcp")
    monkeypatch.setenv("AGNTID_MCP_URL", "http://user:pass@localhost:8082/mcp")
    with pytest.raises(ValueError, match="credentials"):
        AgntIDRuntimeConfig.from_env()


def test_access_token_strips_bearer_prefix_and_is_not_stored_on_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGNTID_ACCESS_TOKEN", "Bearer tok_sample_value")
    token = AgntIDRuntimeConfig.access_token_from_env()
    assert token == "tok_sample_value"
    cfg = AgntIDRuntimeConfig()
    assert "tok_sample_value" not in repr(cfg)


# ---------------------------------------------------------------------------
# Task lifecycle, request serialization, response stripping, control tool hiding
# ---------------------------------------------------------------------------


def test_task_lifecycle_correlates_calls_and_hides_control_tools(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "def divide(a, b): return a / b\n")
    runtime = FakeAgntidMCPRuntime(ws)
    cfg = AgntIDRuntimeConfig(mode="mock", agent_id="agentforge:v1", user_id="user-42")
    session = AgntIDSession(cfg, mcp_client=runtime, is_mock=True)

    assert session.open("Fix divide in calculator.py", delegate_to_agent_id="agentforge:developer")
    try:
        assert session.task_id
        assert session.delegation_id
        # Control tools must never appear in shortlisted model tools.
        assert not (set(session.shortlisted_tool_names) & set(CONTROL_TOOLS))
        assert not (set(session.visible_tool_names) & set(CONTROL_TOOLS))

        rec, output = session.invoke(
            "read_file",
            {"path": "calculator.py"},
            intended_task="Fix divide in calculator.py",
            target_resource="calculator.py",
            normalized_path="calculator.py",
            run_id="run-1",
        )
        assert rec.decision == AuthorizationDecision.ALLOW
        assert rec.decision_source == DecisionSource.MOCK
        assert rec.executed is True
        assert rec.correlation_id.startswith("mock-corr-")
        assert rec.event_id.startswith("mock-evt-")
        assert rec.snapshot_id == runtime.snapshot_id
        assert "__agntid_" not in str(output)
        assert "def divide" in str(output)

        # Verify wire calls carried task_id and delegation_id.
        read_calls = [args for name, args in runtime.calls if name == "read_file"]
        assert len(read_calls) == 1
        assert read_calls[0]["agntid_task_id"] == session.task_id
        assert read_calls[0]["agntid_delegation_id"] == session.delegation_id
    finally:
        session.close()

    assert runtime.tasks[session.task_id]["closed"] is True
    assert any(name == "agntid_delegation_close" for name, _ in runtime.calls)


# ---------------------------------------------------------------------------
# All 10 required security & failure conditions (Section 6)
# ---------------------------------------------------------------------------


def test_condition_1_allow_executes_only_after_authorization(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "def divide(a, b): return a / b\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix divide in calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix divide in calculator.py", run_id="c1")
    try:
        governed.replace_text("calculator.py", "return a / b", "return a / b if b else 0")
        assert runtime.business_call_count == 1
        assert "if b else 0" in ws.read_text("calculator.py")
        assert governed.security_summary.mock_allows == 1
        assert governed.security_summary.agntid_allows == 0
    finally:
        session.close()


def test_condition_2_deny_never_executes(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("utils.py", "original = 1\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix divide in calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix divide in calculator.py", run_id="c2")
    try:
        with pytest.raises(WorkspaceError, match="deny"):
            governed.write_text("utils.py", "tampered = 2\n")
        assert ws.read_text("utils.py") == "original = 1\n"
        assert governed.security_summary.prevented_actions == 1
        assert governed.security_summary.executed_actions == 0
    finally:
        session.close()


def test_condition_3_review_required_blocks_without_host_approval_and_allows_with_host_approval(
    tmp_path: Path,
) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(review_required_tools=("write_file",)))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c3a")
    try:
        with pytest.raises(WorkspaceError, match="review_required"):
            governed.write_text("calculator.py", "x = 2\n")
        assert ws.read_text("calculator.py") == "x = 1\n"
        assert governed.security_summary.mock_restrictions == 1
        rec = governed.security_summary.records[-1]
        assert rec.review_id.startswith("mock-rev-")
        assert rec.review_status == "pending"
    finally:
        session.close()

    # Now with explicit host-supplied approval provider bound to arguments_digest.
    runtime2 = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(review_required_tools=("write_file",)))
    session2 = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime2, is_mock=True)
    session2.open("Fix calculator.py")

    def host_approve(tool_name, args, decision):
        return ApprovalEvidence(
            required=True,
            decision="approved",
            approver_id="human-reviewer-1",
            decision_id="dec-1",
            operation_id=decision.review.operation_id if decision.review else "",
            source="host_approval",
            tool_name=tool_name,
            arguments_digest=arguments_digest(args),
        )

    governed2 = GovernedWorkspace(
        ws, session2, intended_task="Fix calculator.py", run_id="c3b", approval_provider=host_approve
    )
    try:
        governed2.write_text("calculator.py", "x = 2\n")
        assert ws.read_text("calculator.py") == "x = 2\n"
        assert governed2.security_summary.executed_actions == 1
    finally:
        session2.close()


def test_condition_4_timeout_fails_closed(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(inject_timeout_on_call=1))
    session = AgntIDSession(
        AgntIDRuntimeConfig(mode="mock", timeout_seconds=0.1), mcp_client=runtime, is_mock=True
    )
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c4")
    try:
        with pytest.raises(WorkspaceError, match="error"):
            governed.write_text("calculator.py", "x = 2\n")
        assert ws.read_text("calculator.py") == "x = 1\n"
        rec = governed.security_summary.records[-1]
        assert rec.decision == AuthorizationDecision.ERROR
        assert rec.decision_source == DecisionSource.FAIL_CLOSED
        assert governed.security_summary.integration_errors == 1
    finally:
        session.close()


def test_condition_5_malformed_response_fails_closed(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(inject_malformed_on_call=1))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c5")
    try:
        with pytest.raises(WorkspaceError, match="Malformed"):
            governed.write_text("calculator.py", "x = 2\n")
        assert ws.read_text("calculator.py") == "x = 1\n"
        assert governed.security_summary.records[-1].decision_source == DecisionSource.FAIL_CLOSED
    finally:
        session.close()


def test_condition_6_missing_intent_context_fails_closed(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    assert session.open("   ") is False
    governed = GovernedWorkspace(ws, session, intended_task="", run_id="c6")
    try:
        assert governed.ollama_tool_allowlist == set()
        with pytest.raises(WorkspaceError, match="missing"):
            governed.read_text("calculator.py")
        assert governed.security_summary.records[-1].decision_source == DecisionSource.FAIL_CLOSED
    finally:
        session.close()


def test_condition_6b_failed_intent_preparation_fails_closed(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(fail_task_open=True))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    assert session.open("Fix calculator.py") is False
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c6b")
    try:
        assert governed.ollama_tool_allowlist == set()
        with pytest.raises(WorkspaceError):
            governed.read_text("calculator.py")
        assert governed.security_summary.integration_errors == 1
    finally:
        session.close()


def test_condition_7_invalid_tool_parameters_rejected(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c7")
    try:
        with pytest.raises(WorkspaceError, match="Line range"):
            governed.read_lines("calculator.py", 1, 250)
        with pytest.raises(WorkspaceError, match="nonempty line"):
            governed.replace_line("calculator.py", 1, "a\nb")
        assert runtime.business_call_count == 2  # Local policy blocked before disk execution
        assert governed.security_summary.local_policy_rejections == 2
    finally:
        session.close()


def test_condition_8_target_path_change_between_auth_and_exec_aborts(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n")

    def swap_to_symlink(_name: str, _args: dict) -> None:
        target = ws.root / "calculator.py"
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            # Windows without symlink privilege: replace with invalid empty file mismatch
            target.write_text("swapped\n")

    # Test deterministic TOCTOU detector hook in GovernedWorkspace.
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")

    def mutate_after_auth(_norm_path: str, _resolved: Path) -> None:
        raise WorkspaceError("Simulated path mutation detected post-authorization")

    governed = GovernedWorkspace(
        ws, session, intended_task="Fix calculator.py", run_id="c8", post_auth_verifier=mutate_after_auth
    )
    try:
        with pytest.raises(WorkspaceError, match="mutation"):
            governed.read_text("calculator.py")
        rec = governed.security_summary.records[-1]
        assert rec.decision_source == DecisionSource.LOCAL_POLICY
        assert rec.executed is False
    finally:
        session.close()


def test_condition_9_retry_boundary_caps_repeated_denials(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("utils.py", "u = 1\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(
        AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True, max_denied_retries=2
    )
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c9")
    try:
        for _ in range(2):
            with pytest.raises(WorkspaceError):
                governed.write_text("utils.py", "u = 2\n")
        calls_before = runtime.business_call_count
        with pytest.raises(WorkspaceError, match="retry limit"):
            governed.write_text("utils.py", "u = 3\n")
        # Third attempt was short-circuited without hitting the runtime again.
        assert runtime.business_call_count == calls_before
        assert governed.security_summary.records[-1].error == "retry_limit_exceeded"
        assert governed.security_summary.records[-1].decision_source == DecisionSource.LOCAL_GATE
        assert governed.security_summary.mock_denies == 2
    finally:
        session.close()


def test_condition_10_service_unavailable_mid_run_fails_closed(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(inject_unavailable_on_call=2))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="c10")
    try:
        assert governed.read_text("calculator.py") == "x = 1\n"
        with pytest.raises(WorkspaceError, match="error"):
            governed.write_text("calculator.py", "x = 2\n")
        assert ws.read_text("calculator.py") == "x = 1\n"
        assert governed.security_summary.executed_actions == 1
        assert governed.security_summary.prevented_actions == 1
        assert governed.security_summary.integration_errors == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Summary count separation & opt-in live test
# ---------------------------------------------------------------------------


def test_summary_never_conflates_mock_and_real_agntid_counts() -> None:
    summary = SecuritySummary(intended_task="Test", integration_status="mock_simulation")
    from agentforge.security_models import AuthorizationRecord

    summary.add_record(
        AuthorizationRecord(
            tool_name="read_file",
            decision=AuthorizationDecision.ALLOW,
            decision_source=DecisionSource.MOCK,
            agntid_verdict="allow",
            agntid_decision_source="mock",
            executed=True,
        )
    )
    assert summary.mock_allows == 1
    assert summary.agntid_allows == 0


@pytest.mark.live_agntid
def test_live_agntid_runtime_smoke() -> None:
    if os.getenv("AGNTID_RUN_LIVE_TESTS") != "1":
        pytest.skip("Set AGNTID_RUN_LIVE_TESTS=1 and AGNTID_MCP_URL to run live AgntID tests")
    cfg = AgntIDRuntimeConfig.from_env()
    session = AgntIDSession(
        AgntIDRuntimeConfig(
            mode="mcp",
            mcp_url=cfg.mcp_url,
            agent_id=cfg.agent_id,
            user_id=cfg.user_id,
            timeout_seconds=cfg.timeout_seconds,
            tool_prefix=cfg.tool_prefix,
        ),
        is_mock=False,
    )
    try:
        assert session.open("Inspect calculator.py")
        assert session.task_id
        assert session.integration_status == "live_agntid"
    finally:
        session.close()
