# AgentForge

AgentForge is a reference implementation of an agentic software-engineering system. It deliberately combines deterministic orchestration with specialized AI agents instead of turning the entire application into one uncontrolled autonomous loop.

## What it does

A user submits a software task. AgentForge:

1. asks a Supervisor agent to convert the request into an engineering plan;
2. optionally asks a CrewAI planning crew for architecture/QA planning;
3. optionally asks a BeeAI specialist for implementation risks and research notes;
4. gives the plan to a Developer agent with workspace tools;
5. runs deterministic syntax/tests when explicitly enabled for trusted or isolated code;
6. asks a Tester agent to interpret failures;
7. gives failures back to the Developer for bounded repair rounds;
8. asks a Reviewer agent for a final code review;
9. writes a machine-readable run report and a human-readable final report.

The same engine can be invoked through:

- CLI;
- FastAPI;
- a prototype A2A-style facade;
- an optional MCP server.

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

For a local model, start Ollama and choose an installed model such as `qwen2.5-coder:7b` in `.env`. The Ollama adapter calls the local API directly and handles both native tool calls and strict JSON tool requests. Hosted OpenAI requires `OPENAI_API_KEY`.

Docker validation is the default and fails closed if Docker or the validator image is unavailable. Build the image with `docker build -t agentforge-validator:py313 validator`. Use `AGENTFORGE_VALIDATOR=local` only with a trusted fixture or repository.

## Run from CLI

```bash
agentforge build "Build a FastAPI todo API with tests and a README"
```

By default, the result is `unverified`: generated code is not executed. For a repository you trust, or inside a disposable OS/container sandbox, add `--allow-local-execution` to run syntax checks and pytest. A passing syntax check without tests is still `unverified`.

Useful options:

```bash
agentforge build "Build a calculator" --crewai --beeai
agentforge build "Fix the value function" --source-repo C:\path\to\repo --allow-local-execution
```

`--beeai` expects whatever provider/model is configured in `AGENTFORGE_BEEAI_MODEL`; the supplied example uses local Ollama.

## Run the HTTP API

```bash
uvicorn agentforge.api:app --host 127.0.0.1 --port 8000
```

Set a long random `AGENTFORGE_API_TOKEN`. Send `Authorization: Bearer <token>` to POST `/runs` or GET `/runs/{run_id}`. A POST body is:

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

This is a prototype A2A-style facade. Client conformance and authentication have not been established; keep it bound to localhost.

## Run the MCP server

```bash
python -m agentforge.protocols.mcp_server
```

It exposes workspace tools at `http://127.0.0.1:8001/mcp` by default. This optional endpoint has no authentication layer; keep it bound to localhost.

## Run tests

```bash
pytest -q
```

For an offline judge demonstration, run `python -m examples.demo_repair` from the repository root. It uses labeled fake agent responses, executes the actual import/validation/repair/report pipeline against a tiny fixture, and saves `.agentforge-data/demo-report.md`. It does not demonstrate model quality.

For pinned public-repository tests, see [the repository benchmark](docs/REPOSITORY_BENCHMARK.md). It checks real upstream test suites and deliberately broken source in a restricted Docker container, then runs live local-model repairs in GitHub Actions.

## Security model

AgentForge does not give an LLM arbitrary host-shell access. The Developer receives explicit file tools confined to a per-run workspace and cannot edit maintained tests, CI configuration, dependency configuration, or known credential paths. Source import has file and size limits. Docker validation runs without network, with a read-only workspace and resource limits; it still needs host-specific security review. Local validation is not an OS sandbox. Keep all HTTP endpoints bound to localhost. The main API requires a bearer token; the optional A2A/MCP facades do not.

## Framework roles

- **OpenAI Agents SDK**: hosted OpenAI agent runtime. Local Ollama uses a direct API adapter because its native SDK dependency was blocked by Windows Application Control in this environment.
- **CrewAI**: optional autonomous planning/architecture crew.
- **BeeAI**: optional provider-agnostic research/risk specialist.
- **A2A/MCP integrations**: optional local prototype facades; conformance has not been verified.
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

## Existing repository and test policy

Use `agentforge build "Fix the bug" --source-repo C:\path\to\repo --test-manifest C:\path\to\manifest.json --allow-local-execution` in a trusted or isolated environment. The source is copied into a fresh run workspace and its Git commit is recorded. The original directory is not changed. See `docs/POLICIES.md` for trust and publication rules.

A manifest maps changed paths to maintained tests:

```json
{"suites":[{"when":["agentforge/api.py"],"tests":["tests/test_api.py"]},{"when":["agentforge/*"],"tests":["tests/test_engine.py"]}]}
```

Selected tests run first, then the full pytest suite is the final gate. Runs without executable tests are `unverified`; failed checks are `failed`. Use `--generate-tests` to request provisional test suggestions when verification is incomplete. Suggestions are report text and never count as passing evidence. Each attempt records changed paths, baseline diff, and validation output.

## Current scope

The CLI/API, repository import, restricted file tools, bounded repair, and reports form a local prototype. The suite exercises a deterministic failed-test repair with fake agent responses. A real Ollama run on a small maintained fixture passed syntax and pytest after the model edited the copied source. See `docs/STATUS_2026-10-31.md` for exact evidence and remaining blockers. The dashboard, queue, GitHub webhooks/draft PRs, and deployment are not in this checkout. Do not claim production isolation or autonomous PR publication.

# Local prompt dashboard

Run the browser interface from the repository root. For a trusted demo fixture on
Windows PowerShell, start Ollama separately if it is not already running, then:

```powershell
$env:AGENTFORGE_PROVIDER = "ollama"
$env:AGENTFORGE_MODEL = "qwen2.5-coder:7b"
$env:AGENTFORGE_VALIDATOR = "local"
$env:AGENTFORGE_API_TOKEN = "choose-a-long-private-token"
.\.venv\Scripts\python.exe -m agentforge.cli api --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`, enter the same token, keep the maintained example
folder `examples/repair_target`, confirm local execution for this trusted fixture,
and press **Start repair**. The page polls the background job and shows the
actual diff and validation evidence. Jobs are held in server memory; saved run
reports remain under `.agentforge-data/runs/`. A server restart clears job status.
Local validation executes repository tests on the host, so use it only for code
you trust. The default Docker validator remains the choice for unknown code.


The browser demo uses `fast_mode`: it skips the separate model planning, tester, and reviewer calls, then performs the actual model edit and the same deterministic validation and report. This reduces waiting time but does not guarantee a repair will pass. Ollama requests have a six-minute per-call timeout to accommodate a slow first load. Prewarm the model before a timed demo.
