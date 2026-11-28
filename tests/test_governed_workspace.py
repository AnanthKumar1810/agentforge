"""Tests for GovernedWorkspace, local-policy independence, MCP server, and engine integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from agentforge.agents import build_workspace_tools, run_agent
from agentforge.agntid import (
    AgntIDRuntimeConfig,
    AgntIDSession,
    FakeAgntidMCPRuntime,
    FakePolicyConfig,
)
from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine
from agentforge.governed_workspace import GovernedWorkspace
from agentforge.protocols.workspace_mcp import create_workspace_mcp_server
from agentforge.security_models import DecisionSource
from agentforge.workspace import Workspace, WorkspaceError
from agentforge.workspace_routes import WorkspaceRoutes


def test_routed_proxy_isolates_runs_and_revokes_routes(tmp_path: Path) -> None:
    server = create_workspace_mcp_server(tmp_path, routed=True)
    first = Workspace(tmp_path / "first")
    second = Workspace(tmp_path / "second")
    first.write_text("calculator.py", "owner = 'first'\n")
    second.write_text("calculator.py", "owner = 'second'\n")
    (first.root / ".env").write_text("DUMMY_KEY=fixture_only\n")

    class ForwardingTestProxy(FakeAgntidMCPRuntime):
        async def call_tool_async(self, name, arguments):
            if name.startswith("agntid_"):
                return await super().call_tool_async(name, arguments)
            if arguments.get("agntid_task_id") not in self.tasks:
                raise AssertionError("Task attribution missing")
            payload = {key: value for key, value in arguments.items() if not key.startswith("agntid_")}
            _, structured = await server.call_tool(name, payload)
            return {
                "__agntid_result": {"structuredContent": structured},
                "__agntid_policy_decision": {"allowed": True, "policy_name": "test-policy"},
            }

    config = AgntIDRuntimeConfig(mode="mcp")
    task = "Update calculator.py"
    proxy = ForwardingTestProxy(first, policy=FakePolicyConfig(allow_all_for_test=True))
    # Separate runtime transports, as each session owns its MCP connection.
    sessions = [
        AgntIDSession(config, mcp_client=client, is_mock=True)
        for client in (proxy, ForwardingTestProxy(second, policy=FakePolicyConfig(allow_all_for_test=True)))
    ]
    try:
        for session, workspace in zip(sessions, (first, second), strict=True):
            session.bind_workspace(workspace, tmp_path)
            assert session.open(task)
        id_a, id_b = (session.workspace_id for session in sessions)
        assert id_a and id_b and id_a != id_b
        run_a, run_b = (
            GovernedWorkspace(workspace, session, task)
            for workspace, session in zip((first, second), sessions, strict=True)
        )
        assert "first" in run_a.read_text("calculator.py")
        assert "second" in run_b.read_text("calculator.py")
        run_a.write_text("calculator.py", "owner = 'updated first'\n")
        assert "second" in run_b.read_text("calculator.py")
        assert "updated first" in first.read_text("calculator.py")
        assert id_a not in run_a.security_summary.model_dump_json()

        for arguments, error in (
            ({"path": "calculator.py"}, "route_unavailable"),
            ({"path": "calculator.py", "workspace_id": "../first"}, "route_unavailable"),
            ({"path": "../second/calculator.py", "workspace_id": id_a}, "workspace_action_failed"),
            ({"path": ".env", "workspace_id": id_a}, "workspace_action_failed"),
        ):
            _, result = asyncio.run(server.call_tool("read_file", arguments))
            assert error in str(result)
            assert "fixture_only" not in str(result)

        sessions[0].close()
        with pytest.raises(WorkspaceError, match="route is invalid or unavailable"):
            WorkspaceRoutes(tmp_path).resolve(id_a, max_file_bytes=1000)
        assert "second" in run_b.read_text("calculator.py")
    finally:
        for session in sessions:
            session.close()


@pytest.mark.parametrize("same_root", [True, False])
def test_proxy_must_prove_workspace_identity_before_writing(tmp_path: Path, same_root: bool) -> None:
    workspace = Workspace(tmp_path / "run")
    proxy_workspace = workspace if same_root else Workspace(tmp_path / "unrelated")
    proxy_workspace.write_text("calculator.py", "original = True\n")
    runtime = FakeAgntidMCPRuntime(proxy_workspace, policy=FakePolicyConfig(allow_all_for_test=True))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mcp"), mcp_client=runtime, is_mock=True)
    task = "Update calculator.py"
    try:
        assert session.open(task)
        governed = GovernedWorkspace(workspace, session, task)
        if same_root:
            governed.write_text("calculator.py", "updated = True\n")
            assert proxy_workspace.read_text("calculator.py") == "updated = True\n"
        else:
            for _ in range(2):
                with pytest.raises(WorkspaceError, match="proxy could not prove"):
                    governed.write_text("calculator.py", "updated = True\n")
            assert proxy_workspace.read_text("calculator.py") == "original = True\n"
            assert runtime.business_call_count == 1
            assert governed.security_summary.integration_errors == 2
        assert list(workspace.root.glob(".agentforge-workspace-*")) == []
    finally:
        session.close()


def test_audit_records_omit_file_contents_search_terms_and_write_payloads(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "run")
    value = "dummy_private_value"
    workspace.write_text("calculator.py", f"sample = '{value}'\n")
    runtime = FakeAgntidMCPRuntime(workspace, policy=FakePolicyConfig(allow_all_for_test=True))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    task = "Update calculator.py"
    try:
        assert session.open(task)
        governed = GovernedWorkspace(workspace, session, task)
        assert value in governed.read_text("calculator.py")
        assert value in governed.search_files(value, "calculator.py")
        governed.write_text("calculator.py", f"new_sample = '{value}'\n")
        assert value not in governed.security_summary.model_dump_json()
        assert governed.security_summary.executed_actions == 3
    finally:
        session.close()


def test_agntid_allow_never_overrides_local_workspace_restrictions(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws", max_file_bytes=200)
    ws.write_text("calculator.py", "def add(a, b): return a + b\n")
    (ws.root / ".env").write_text("DUMMY_SECRET=should_never_be_read\n")
    (ws.root / "tests").mkdir()
    (ws.root / "tests" / "test_calc.py").write_text("assert True\n")

    # Configure mock runtime to ALLOW everything — local controls must still block!
    runtime = FakeAgntidMCPRuntime(ws, policy=FakePolicyConfig(allow_all_for_test=True))
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Refactor everything")
    governed = GovernedWorkspace(ws, session, intended_task="Refactor everything", run_id="local-guard")
    try:
        with pytest.raises(WorkspaceError, match="Credential"):
            governed.read_text(".env")
        with pytest.raises(WorkspaceError, match="Protected"):
            governed.write_text("tests/test_calc.py", "assert False\n")
        with pytest.raises(WorkspaceError, match="Protected"):
            governed.write_text("pyproject.toml", "[project]\nname='x'\n")
        with pytest.raises(WorkspaceError, match="Parent-directory traversal"):
            governed.read_text("../outside.txt")
        with pytest.raises(WorkspaceError, match="Absolute paths|outside"):
            governed.write_text("/tmp/escape.py", "x=1\n")
        with pytest.raises(WorkspaceError, match="Absolute paths"):
            governed.write_text("C:\\temp\\escape.py", "x=1\n")
        with pytest.raises(WorkspaceError, match="size limit"):
            governed.write_text("big.py", "x" * 201)

        summary = governed.security_summary
        assert summary.executed_actions == 0
        assert summary.local_policy_rejections == 7
        assert all(rec.decision_source == DecisionSource.LOCAL_POLICY for rec in summary.records)
        assert all(rec.executed is False for rec in summary.records)
    finally:
        session.close()


def test_build_workspace_tools_respects_agntid_shortlist(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "x = 1\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix calculator.py", run_id="shortlist")
    try:
        # Simulate narrowed shortlist allowing only read_file and list_files
        governed.ollama_tool_allowlist = {"read_file", "list_files"}
        tools = build_workspace_tools(governed)
        assert [t.name for t in tools] == ["read_file", "list_files"]
    finally:
        session.close()


def test_ollama_loop_through_governed_workspace_avoids_duplicate_bookkeeping_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentforge.agents as agents_mod

    ws = Workspace(tmp_path / "ws")
    ws.write_text("calculator.py", "def divide(a, b): return a / b\n")
    runtime = FakeAgntidMCPRuntime(ws)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=runtime, is_mock=True)
    session.open("Fix divide in calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix divide in calculator.py", run_id="ollama-gov")

    replies = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "replace_text",
                            "arguments": {
                                "path": "calculator.py",
                                "old": "return a / b",
                                "new": "return a / b if b else 0",
                            },
                        }
                    }
                ],
            }
        },
        {"message": {"role": "assistant", "content": "Fixed divide."}},
    ]
    monkeypatch.setattr(agents_mod, "_ollama_chat", lambda *_a: replies.pop(0))

    try:
        out = asyncio.run(
            run_agent(
                "Developer",
                "Fix divide",
                "Fix divide in calculator.py",
                tools=[governed],
                provider="ollama",
            )
        )
        assert out == "Fixed divide."
        # Exactly one governed tool record (replace_text); internal before/after tracking didn't emit read_file records.
        assert governed.security_summary.total_attempts == 1
        assert governed.security_summary.records[0].tool_name == "replace_text"
        assert governed.security_summary.executed_actions == 1
    finally:
        session.close()


def test_engine_mock_agntid_end_to_end_and_preserves_report_on_midrun_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentforge.engine as engine_module

    monkeypatch.setattr(engine_module, "build_workspace_tools", lambda ws: [ws])
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))

    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "calculator.py").write_text("def divide(a, b): return a / b\n")
    (source / "utils.py").write_text("def helper(): return 1\n")
    (source / "tests" / "test_calculator.py").write_text(
        "from calculator import divide\n"
        "def test_ok(): assert divide(10, 2) == 5\n"
        "def test_zero(): assert divide(10, 0) == 0\n"
    )

    async def governed_agent(name, instructions, prompt, *, tools=None, model="unused"):
        if name == "Developer":
            ws = tools[0]
            # Attempt unauthorized cross-file edit first
            with pytest.raises(WorkspaceError):
                ws.write_text("utils.py", "tampered\n")
            # Perform authorized repair in calculator.py
            ws.replace_text("calculator.py", "return a / b", "return 0 if b == 0 else a / b")
        return f"{name} done"

    settings = Settings(
        model="demo",
        data_dir=tmp_path / "data",
        max_repair_rounds=0,
        validation_timeout_seconds=20,
        max_file_bytes=100_000,
        beeai_model="unused",
        validator="local",
        agntid_mode="mock",
    )
    report = asyncio.run(
        AgentForgeEngine(settings, agent_call=governed_agent).run(
            "Fix divide in calculator.py",
            source_repo=str(source),
            allow_local_execution=True,
        )
    )
    assert report.status == "passed"
    assert report.security is not None
    assert report.security.integration_status == "mock_simulation"
    assert report.security.total_attempts == 2
    assert report.security.mock_denies == 1
    assert report.security.mock_allows == 1
    assert report.security.executed_actions == 1
    assert report.security.prevented_actions == 1
    assert Path(report.artifacts["security_markdown"]).is_file()
    assert Path(report.artifacts["security_json"]).is_file()

    # Now verify report is STILL preserved when authorization fails closed mid-run.
    failing_runtime = FakeAgntidMCPRuntime(
        Workspace(tmp_path / "unused"), policy=FakePolicyConfig(fail_task_open=True)
    )
    failing_report = asyncio.run(
        AgentForgeEngine(settings, agent_call=governed_agent).run(
            "Fix divide in calculator.py",
            source_repo=str(source),
            allow_local_execution=False,
            agntid_mode="mock",
            agntid_client=failing_runtime,
        )
    )
    assert failing_report.security is not None
    assert failing_report.security.integration_status == "mock_simulation" or failing_report.security.integration_errors >= 1
    assert Path(failing_report.artifacts["markdown"]).is_file()
    assert Path(failing_report.artifacts["security_markdown"]).is_file()


def test_workspace_mcp_server_enforces_workspace_protections(tmp_path: Path) -> None:
    root = tmp_path / "mcp_ws"
    root.mkdir()
    (root / "calculator.py").write_text("def add(a, b): return a + b\n")
    (root / ".env").write_text("DUMMY=123\n")
    server = create_workspace_mcp_server(root)

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"read_file", "read_lines", "search_files", "write_file", "replace_text", "replace_line", "list_files"} <= names

    _, read_res = asyncio.run(server.call_tool("read_file", {"path": "calculator.py"}))
    assert "def add" in str(read_res)
    _, env_res = asyncio.run(server.call_tool("read_file", {"path": ".env"}))
    assert "ERROR:" in str(env_res) and "123" not in str(env_res)
    _, trav_res = asyncio.run(server.call_tool("read_file", {"path": "../secret.txt"}))
    assert "ERROR:" in str(trav_res)


def test_agntid_session_against_real_fastmcp_in_memory_proxy(tmp_path: Path) -> None:
    """Exercise AgntIDSession + wrap_client against a real FastMCP server over MCP CallToolResult."""
    import json
    from fastmcp import Client, FastMCP

    ws_root = tmp_path / "fastmcp_ws"
    ws = Workspace(ws_root)
    ws.write_text("calculator.py", "def divide(a, b): return a / b\n")
    ws.write_text("utils.py", "def helper(): return 1\n")

    proxy = FastMCP("MockAgntIDProxy")
    active_tasks: dict[str, str] = {}

    @proxy.tool()
    def agntid_task_open(task_id: str, agent_id: str, user_id: str, prompt: str, execution_context: dict | None = None) -> dict:
        active_tasks[task_id] = prompt
        return {
            "ok": True,
            "intent": {
                "prepared": True,
                "eligible_tool_count": 2,
                "relevant_tool_count": 2,
                "no_relevant_tools": False,
                "snapshot": {
                    "id": "fastmcp-snap-1",
                    "engine_id": "mock-fastmcp",
                    "task_id": task_id,
                    "relevant_tool_ids": ["read_file", "replace_text"],
                    "relevant_tool_names": ["read_file", "replace_text"],
                },
            },
        }

    @proxy.tool()
    def agntid_task_close(task_id: str) -> dict:
        active_tasks.pop(task_id, None)
        return {"ok": True}

    @proxy.tool()
    def read_file(path: str, agntid_task_id: str = "") -> dict:
        if agntid_task_id not in active_tasks:
            raise ValueError(json.dumps({"__agntid_error": "Task closed", "__agntid_policy_decision": {"allowed": False, "reason": "No task"}}))
        return {
            "__agntid_result": ws.read_text(path),
            "__agntid_policy_decision": {"allowed": True, "policy_name": "fastmcp-allow", "correlation_id": "fm-1"},
            "__agntid_execution_flow": {"status": "success", "event_id": "fm-evt-1"},
        }

    @proxy.tool()
    def replace_text(path: str, old: str, new: str, agntid_task_id: str = "") -> dict:
        if path != "calculator.py":
            raise ValueError(json.dumps({
                "__agntid_error": "Out of scope",
                "__agntid_policy_decision": {"allowed": False, "reason": "Write restricted to calculator.py", "policy_name": "fastmcp-intent"},
            }))
        return {
            "__agntid_result": ws.replace_text(path, old, new),
            "__agntid_policy_decision": {"allowed": True, "policy_name": "fastmcp-allow", "correlation_id": "fm-2"},
            "__agntid_execution_flow": {"status": "success", "event_id": "fm-evt-2"},
        }

    client = Client(proxy)
    session = AgntIDSession(AgntIDRuntimeConfig(mode="mock"), mcp_client=client, is_mock=True)
    assert session.open("Fix divide in calculator.py")
    governed = GovernedWorkspace(ws, session, intended_task="Fix divide in calculator.py", run_id="fastmcp-1")
    try:
        assert "def divide" in governed.read_text("calculator.py")
        governed.replace_text("calculator.py", "return a / b", "return 0 if b == 0 else a / b")
        assert "if b == 0" in ws.read_text("calculator.py")
        with pytest.raises(WorkspaceError):
            governed.replace_text("utils.py", "return 1", "return 2")
        assert "return 1" in ws.read_text("utils.py")
    finally:
        session.close()
