# AgntID Runtime Integration (`agntid-sdk`)

## 1. Overview & Security Problem

AI coding agents commonly receive broad workspace or tool capabilities via static configuration. Even when instructed in system prompts not to read secrets or edit unrelated files, prompt injection, task drift, or reasoning mistakes can cause the agent to invoke tools outside the user's intended scope.

AgentForge pairs its deterministic local workspace controls (`Workspace`) with **AgntID** (`AGNTID-AI/agntid-python`, `agntid-sdk` v0.2.0) to evaluate tool actions against the user's **task intent** at runtime:

```text
User Task
    |
    v
AgentForgeEngine (Supervisor -> Plan -> ExecutionContext)
    |
    v
AgntIDSession.open()
  - create_task(agent_id, user_id, prompt)
  - send_task_open_details(..., execution_context)
  - filter_tools_for_task()          [fails closed on IntentPreparationError]
  - send_delegation_open()           [Supervisor -> Developer lineage]
    |
    v
GovernedWorkspace (single boundary for OpenAI Agents SDK & Ollama)
  1. Validate non-empty intended_task
  2. Normalize & check path containment (Workspace.resolve)
  3. Evaluate local AgentForge policy (secrets, tests, size, line bounds)
  4. Bind SHA-256 arguments_digest + normalized path
  5. wrapped.call_tool_async(...) -> AgntID MCP Runtime (or FakeAgntidMCPRuntime)
  6. Parse decision (__agntid_policy_decision / DenialResult / ReviewDecision)
  7. Verify local policy was NOT overridden & re-verify target path (anti-TOCTOU)
    |
    +--- allow -----------> Result sanitized (__agntid_* stripped) & returned
    +--- deny ------------> Action prevented, WorkspaceError raised
    +--- review_required -> Fails closed unless host supplies ApprovalEvidence
    +--- error/timeout ---> Fails closed (DecisionSource.FAIL_CLOSED)
    |
    v
RunReport + security_report.json + security_report.md
```

---

## 2. Official AgntID Interfaces Used

All integration code in [`agentforge/agntid.py`](../agentforge/agntid.py) uses only documented interfaces from `AGNTID-AI/agntid-python` (`agntid-sdk` v0.2.0):

| Interface | Role in AgentForge |
|---|---|
| `agntid.AgntidMCPClient(mcp_url, access_token=...)` | Connects to the AgntID MCP proxy using raw `AGNTID_ACCESS_TOKEN` on transport only |
| `agntid.get_tools_list(client)` | Discovers profile-visible MCP tools |
| `agntid.create_task(agent_id, user_id, prompt)` | Generates SDK-controlled UUID `task_id` per run |
| `agntid.ExecutionContext`, `FrameworkInfo`, `ConversationContext`, `ExecutionPlan`, `PlannedAction` | Sends structured Supervisor plan and `extract_explicit_constraints(prompt)` at task open |
| `agntid.send_task_open_details(...)` & `agntid.filter_tools_for_task(...)` | Registers intent and narrows visible tools; raises `IntentPreparationError` (fails closed) if unavailable |
| `agntid.DelegationContext`, `DelegatedIntent`, `send_delegation_open`, `send_delegation_close` | Captures Supervisor -> Developer delegation lineage |
| `agntid.wrap_client(client, task_id=..., delegation_id=...)` | Injects `agntid_task_id` / `agntid_delegation_id` and strips `__agntid_*` fields before LLM consumption |
| `agntid.parse_decision`, `parse_decision_from_exception`, `get_last_denial` | Extracts structured `DecisionOutcome` (`allow`, `deny`, `review_required`), `ReviewDecision`, `policy_name`, `correlation_id`, `event_id`, `snapshot_id` |
| `agntid.arguments_digest` | SHA-256 binds tool parameters and normalized paths between authorization and execution |
| `agntid.send_task_close(client, task_id)` | Closes task cleanly in `finally` even on agent failure or timeout |

Control tools (`agntid_task_open`, `agntid_task_close`, `agntid_delegation_open`, `agntid_delegation_close`, `agntid_action_update`) are **never** exposed to the Developer model.

---

## 3. Workspace MCP Server for AgntID Proxying

AgentForge provides [`agentforge/protocols/workspace_mcp.py`](../agentforge/protocols/workspace_mcp.py) (`agentforge workspace-mcp`), exposing its 7 `Workspace`-confined coding tools over MCP Streamable HTTP (`127.0.0.1:8003` by default):

- `read_file`
- `read_lines`
- `search_files`
- `write_file`
- `replace_text`
- `replace_line`
- `list_files`

When an operator connects a local AgntID Runtime (`docker compose up -d` from `AGNTID-AI/agntid-python`) to the AgntID portal, registers this MCP server in **Operations -> MCP -> Servers**, and approves classifications in **Access Control -> Tools**, AgentForge routes tool execution through `AGNTID_MCP_URL` (`http://localhost:8082/mcp`).

Start the tool server with `workspace-mcp --routed`. The runner and the tool server share `AGENTFORGE_MCP_WORKSPACE_ROOT` (defaults to `AGENTFORGE_DATA_DIR`). The host generates a temporary opaque route for each disposable workspace, injects it into AgntID tool calls, and revokes it when closing the task. The tool server looks up the route and enforces the workspace boundary and the stricter file-size limit before acting. It returns a structured workspace result so AgentForge can distinguish a successful execution from a route or tool error. Protect the shared directory and limit access to the tool server to the trusted runtime. See [the Windows setup walkthrough](HACKATHON_DEMO.md#3-register-and-run-the-live-demo).

---

## 4. Environment Variables

Documented AgntID variables:

- `AGNTID_MCP_URL`: AgntID Runtime MCP proxy endpoint (default `http://localhost:8082/mcp`).
- `AGNTID_ACCESS_TOKEN`: Optional raw OAuth access token when the runtime enforces client OAuth (do not include `Bearer `; never logged or persisted in `RunReport`).
- `AGNTID_AGENT_ID`: Logical agent ID attributed to AgntID (default `agentforge-coding-agent:v1`).
- `AGNTID_USER_ID`: Trusted operator/session subject (default `local-operator`).

AgentForge integration toggles:

- `AGENTFORGE_AGNTID_MODE`: `off` (default), `mcp` (live AgntID MCP Runtime; fails closed if unreachable), or `mock` (explicitly labelled offline simulation).
- `AGENTFORGE_AGNTID_TIMEOUT_SECONDS`: Bounded per-call timeout (default `10.0`).
- `AGENTFORGE_AGNTID_TOOL_PREFIX`: Optional tool name prefix if the AgntID catalog namespaces tools.

---

## 5. Defense-in-Depth & Fail-Closed Guarantees

1. **AgntID approval never overrides AgentForge local policy:** Even if AgntID (or a mock policy) returns `allow`, `GovernedWorkspace` independently rejects `.env*`, `.aws/`, `.ssh/`, `*.pem`, `*.key`, `tests/`, `test_*.py`, `*_test.py`, `PROTECTED_FILES`, parent traversal (`..`), absolute paths, symlinks, and oversized files.
2. **Clear source attribution:** Reports distinguish `agntid` (real live AgntID runtime), `mock` (offline simulation), `local_policy` (AgentForge local workspace block), and `fail_closed` (timeout, unavailable runtime, missing intent, malformed response).
3. **Bounded retries and path checks:** The client normalizes the target path before submission and checks it again when the proxy returns. This client-side check cannot undo an operation already forwarded by the proxy. The downstream Workspace independently validates paths, and the retry cap defaults to 3.
4. **Payload minimization:** Security records keep result status, argument lengths, and runtime evidence identifiers. Direct tool results, search strings, and write payloads are omitted from these records. The ordinary run report can still include the supplied task, project diffs, and reviewer output.

---

## 6. Current Verification Status

| Component / Capability | Status |
|---|---|
| `agntid-sdk` (`0.2.0`) installed in `.venv` | **Implemented & verified** |
| `AgntIDSession`, `GovernedWorkspace`, `SecuritySummary`, `security_reporting` | **Implemented & tested** (unit tests + in-memory `FastMCP` proxy tests + `FakeAgntidMCPRuntime`) |
| `agentforge workspace-mcp` server | **Implemented & tested** against `Workspace` controls |
| Offline 8-experiment suite (`python -m examples.security_experiments`) | **Implemented & verified** (`mock_simulation`) |
| Live AgntID Runtime (`achr-runtime`) end-to-end verification | **Implemented, NOT live-verified** (requires AgntID Portal account, bootstrap token, running Docker Compose runtime, and portal tool approval) |
| Interactive multi-turn human-review polling UI | **Unsupported / Planned** (host `approval_provider` hook and `REVIEW_REQUIRED` fail-closed handling are implemented and tested) |
