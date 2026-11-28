# AgentForge × AgntID Hackathon Demo Guide

## Live vs Offline Labelling

- **Offline Simulation (`mock_simulation`):** Available immediately with no external dependencies. Runs the full `AgentForgeEngine`, `agntid` SDK wire contract (`create_task`, `send_task_open_details`, `filter_tools_for_task`, `wrap_client`, `parse_decision`), `GovernedWorkspace`, deterministic `pytest` validation, and Markdown/JSON reporting against `FakeAgntidMCPRuntime`. All decisions are explicitly labelled `mock` (not live AgntID).
- **Live AgntID Mode (`live_agntid`):** Requires a registered runtime and the routed AgentForge MCP server. Live results require runtime evidence in both tasks.

---

## Commands

### 1. Run the 11-step End-to-End Security Demo (Offline Simulation)

```powershell
.\.venv\Scripts\python.exe -m examples.demo_agntid_security
```

Artifacts produced:
- `.agentforge-data/demo-agntid/run1-report.md`
- `.agentforge-data/demo-agntid/run1-security-report.md`
- `.agentforge-data/demo-agntid/run2-security-report.md`

### 2. Run the 8-Category Reproducible Experiment Suite

```powershell
.\.venv\Scripts\python.exe -m examples.security_experiments
```

Artifacts produced:
- `.agentforge-data/security/reports/security_report.md`
- `.agentforge-data/security/reports/security_report.json`
- `.agentforge-data/security/experiments/suite_<id>.json`

### 3. Register and Run the Live Demo

Open Docker Desktop in Linux-container mode and check that `docker version` lists a Server. Use the [official AgntID runtime setup guide](https://github.com/AGNTID-AI/agntid-python/blob/main/examples/README.md#2-register-and-start-an-agntid-runtime) to clone the SDK repository, start its Compose stack, and register the runtime. Enter the one-time bootstrap token only in the local runtime interface at `http://localhost:8080`. Use the guide's starter-tool check before adding AgentForge.

From the AgentForge source directory, install the dependencies and run the routed tool server:

```powershell
python -m pip install -e '.[agntid]'
$env:AGENTFORGE_MCP_WORKSPACE_ROOT = Join-Path $PWD.Path '.agentforge-data'
python -m agentforge.cli workspace-mcp --routed --host 127.0.0.1 --port 8003
```

In the portal, register the AgentForge Streamable HTTP MCP endpoint under **Operations → MCP → Servers**. For a Docker Desktop runtime, try `http://host.docker.internal:8003/mcp` from the container. If the runtime cannot reach the loopback listener, bind the server to the private interface reachable by Docker and restrict inbound access accordingly. Wait for the catalog to sync and review the needed tool classifications under **Access Control → Tools**. If the catalog was previously registered, refresh it to pick up the `workspace_id` argument.

Run the demo in another PowerShell window from the same AgentForge directory:

```powershell
$env:AGENTFORGE_MCP_WORKSPACE_ROOT = Join-Path $PWD.Path '.agentforge-data'
$env:AGNTID_MCP_URL = 'http://127.0.0.1:8082/mcp'
python -m examples.demo_agntid_security --live
```

If OAuth is enabled, supply the current raw MCP access token to the trusted process as `AGNTID_ACCESS_TOKEN`. If the runtime catalogs namespaced tools, set `AGENTFORGE_AGNTID_TOOL_PREFIX` to the prefix shown in the catalog. Use the two security reports and the portal's Reporting view to corroborate verdicts using event or correlation IDs. A shortlist exclusion, local protection, or connection error does not count as a runtime action verdict. The demo exits nonzero when either coding run fails or either intent lacks an AgntID verdict for the identical `utils.py` edit.

---

## Three-Minute Hackathon Presentation Sequence

1. **0:00–0:40 — Problem & Architecture:** Show how AgentForge restricts coding agents to 7 `Workspace` tools without shell access, and places `AgntIDSession` + `GovernedWorkspace` directly across the unified tool boundary (`OpenAI Agents SDK` & `Ollama`).
2. **0:40–1:40 — Live/Simulated Run 1 (`python -m examples.demo_agntid_security`):** Give the agent `"Fix the division-by-zero handling in calculator.py."` Show `read_file("calculator.py")` and `replace_text("calculator.py")` succeeding, while `read_file(".env")` and `write_file("utils.py")` are blocked before execution and `pytest` verifies the repair (`status=passed`).
3. **1:40–2:30 — Intent Change & Defense-in-Depth (`Run 2` & `EXP-07`):** Switch to `"Refactor the calculator module and its supporting utilities."` Show `utils.py` flipping from `deny` to `allow`, while `test_calculator.py` stays blocked by AgentForge's maintained-test protection even when intent allows test edits.
4. **2:30–3:00 — Fail-Closed Evidence (`security_report.md`):** Open `.agentforge-data/security/reports/security_report.md`, highlighting separate counts (`agntid` vs `mock` vs `local_policy` vs `fail_closed`), correlation/snapshot IDs, and timeout/malformed-response fail-closed experiments (`EXP-08f`–`EXP-08i`).
