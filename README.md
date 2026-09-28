# AgentForge

AgentForge is a reference implementation of an agentic software-engineering system. It deliberately combines deterministic orchestration with specialized AI agents instead of turning the entire application into one uncontrolled autonomous loop.

## What it does

A user submits a software task. AgentForge:

1. asks a Supervisor agent to convert the request into an engineering plan;
2. optionally asks a CrewAI planning crew for architecture/QA planning;
3. optionally asks a BeeAI specialist for implementation risks and research notes;
4. gives the plan to a Developer agent with workspace tools;
5. runs deterministic syntax/tests in a controlled subprocess;
6. asks a Tester agent to interpret failures;
7. gives failures back to the Developer for bounded repair rounds;
8. asks a Reviewer agent for a final code review;
9. writes a machine-readable run report and a human-readable final report.

The same engine can be invoked through:

- CLI;
- FastAPI;
- an A2A 1.0-compatible server;
- MCP v2 workspace tools.

## Architecture

```text
User / API / A2A
        |
        v
+-------------------+
| Deterministic     |
| Orchestrator      |
+---------+---------+
          |
          +--> Supervisor (OpenAI Agents SDK)
          |
          +--> CrewAI Planning Crew (optional)
          |
          +--> BeeAI Research Specialist (optional)
          |
          +--> Developer (OpenAI Agents SDK + file tools)
          |
          +--> Deterministic Validator
          |       | compileall
          |       + pytest when tests are present
          |
          +--> Tester (OpenAI Agents SDK)
          |       |
          |       +--> bounded repair loop --> Developer
          |
          +--> Reviewer (OpenAI Agents SDK)
          |
          v
     RunReport + artifacts
```

MCP is used for agent-to-tool exposure. A2A is used for agent-to-agent/service interoperability. They solve different layers of the system.

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -e ".[all]"
copy .env.example .env  # Windows
# cp .env.example .env  # Linux/macOS
```

Add your API key to `.env`.

## Run from CLI

```bash
agentforge build "Build a FastAPI todo API with tests and a README"
```

Useful options:

```bash
agentforge build "Build a calculator" --crewai --beeai
```

`--beeai` expects whatever provider/model is configured in `AGENTFORGE_BEEAI_MODEL`; the supplied example uses local Ollama.

## Run the HTTP API

```bash
uvicorn agentforge.api:app --host 127.0.0.1 --port 8000
```

Then POST to `/runs`:

```json
{
  "request": "Build a Python command-line todo app with tests",
  "use_crewai": false,
  "use_beeai": false
}
```

## Run the A2A server

```bash
python -m agentforge.protocols.a2a_server
```

The public Agent Card is exposed by the A2A SDK at the standard well-known path. The server delegates an incoming A2A task to the complete AgentForge engine.

## Run the MCP server

```bash
python -m agentforge.protocols.mcp_server
```

It exposes safe workspace tools over MCP v2 at `http://127.0.0.1:8001/mcp` by default.

## Run tests

```bash
pytest -q
```

## Security model

AgentForge does not give an LLM arbitrary host-shell access. The Developer receives explicit file tools confined to a per-run workspace. Validation subprocesses are issued by deterministic Python code rather than by the model, use `shell=False`, have timeouts, and receive a reduced environment. This is a reference design, not a hardened remote-code-execution platform; production deployments should use OS/container isolation for generated code.

## Framework roles

- **OpenAI Agents SDK**: core Supervisor, Developer, Tester, Reviewer runtime and tool calling.
- **CrewAI**: optional autonomous planning/architecture crew.
- **BeeAI**: optional provider-agnostic research/risk specialist.
- **A2A 1.0**: standardized external agent discovery/delegation surface.
- **MCP v2**: standardized workspace tool surface.
- **FastAPI**: normal application/API boundary.
- **Pydantic**: typed reports and validation.

## Documentation

Read `docs/ENGINEERING_NOTES.md` first. It explains what was built, why each decision was made, how the execution flow works, the trade-offs, and what to improve for production.

## Official references used when designing this repository

- A2A: https://a2a-protocol.org/
- A2A Python SDK: https://a2a-protocol.org/latest/sdk/python/
- MCP Python SDK: https://py.sdk.modelcontextprotocol.io/
- CrewAI: https://docs.crewai.com/
- BeeAI Framework: https://framework.beeai.dev/
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
