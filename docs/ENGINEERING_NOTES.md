# AgentForge engineering notes

## Lifecycle

1. Validate the request size and repair-round limit.
2. Create a unique per-run workspace and artifacts folder.
3. Ask the Supervisor for a plan and acceptance criteria.
4. Optionally run CrewAI planning and/or a BeeAI risk review; these integrations do not receive workspace write tools.
5. Give the Developer only read, write, and list workspace tools.
6. Run deterministic `compileall`; run `pytest` if test files exist. Commands are fixed by Python, use `shell=False`, have timeouts, and use a reduced environment.
7. On validation failure, ask the Tester to interpret captured evidence and give it to the Developer for a bounded repair loop (0–5 rounds).
8. Ask a Reviewer to assess the evidence and save machine-readable JSON and Markdown reports.

## Security and limitations

- Workspace paths reject absolute paths, parent traversal, and paths resolving outside the run workspace.
- The LLM has no shell tool, but deterministic validation does execute generated Python. Path checks and a reduced environment are not an OS-level sandbox; generated code may still access resources available to the current user. Use a disposable container/VM with resource and network controls for untrusted tasks.
- Never commit `.env` or API keys.
- CrewAI and BeeAI are optional adapters. Check their installed SDK versions and provider configuration before enabling them.
- `protocols/a2a_server.py` is a small JSON-RPC/A2A-style interoperability prototype, not a conformance-certified A2A 1.0 implementation. Validate the payloads against the client SDK you intend to use.
- MCP server mode uses the Python MCP SDK streamable HTTP transport; it is opt-in and should be bound to localhost unless separately secured.

## Setup and verification

Use Python 3.11–3.13:

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[all,dev]"
cp .env.example .env
# Set OPENAI_API_KEY in .env
pytest -q
agentforge build "Create a Python calculator with tests"
```

Run the HTTP API using `agentforge api --host 127.0.0.1 --port 8000`.
