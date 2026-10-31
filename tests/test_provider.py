"""Provider configuration and local Ollama tool-call behavior."""
import asyncio
from agentforge.config import Settings
from agentforge.agents import run_agent
from agentforge.workspace import Workspace


def test_ollama_configuration_and_local_only_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFORGE_PROVIDER", "ollama")
    monkeypatch.delenv("AGENTFORGE_MODEL", raising=False)
    monkeypatch.setenv("AGENTFORGE_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.model == "qwen3:8b"
    assert settings.validator == "docker"
    monkeypatch.setenv("AGENTFORGE_OLLAMA_BASE_URL", "http://example.com/v1/")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "local" in str(exc)
    else:
        raise AssertionError("remote endpoint accepted")


def test_ollama_direct_tool_call(tmp_path, monkeypatch):
    import agentforge.agents as module
    responses = [
        {"message": {"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "write_file",
                                                  "arguments": {"path": "code.py", "content": "value = 2\n"}}}]}},
        {"message": {"role": "assistant", "content": "Fixed the value."}},
    ]
    observed = []

    def chat(endpoint, payload):
        observed.append(payload)
        return responses.pop(0)

    monkeypatch.setattr(module, "_ollama_chat", chat)
    result = asyncio.run(run_agent("Developer", "instructions", "Fix the value",
                                   tools=[Workspace(tmp_path)], model="qwen2.5-coder:7b",
                                   provider="ollama"))
    assert result == "Fixed the value."
    assert (tmp_path / "code.py").read_text() == "value = 2\n"
    assert observed[1]["messages"][-1]["role"] == "tool"


def test_ollama_text_json_tool_call(tmp_path, monkeypatch):
    import agentforge.agents as module
    replies = [
        {"message": {"role": "assistant", "content":
                     '```json\n{"name":"write_file","arguments":{"path":"sample.py","content":"def value(): return 2"}}\n```'}},
        {"message": {"role": "assistant", "content": "Done."}},
    ]
    monkeypatch.setattr(module, "_ollama_chat", lambda endpoint, payload: replies.pop(0))
    result = asyncio.run(run_agent("Developer", "instructions", "Fix value",
                                   tools=[Workspace(tmp_path)], provider="ollama"))
    assert result == "Done."
    assert "return 2" in (tmp_path / "sample.py").read_text()


def test_repeated_ollama_write_finishes_without_loop(tmp_path, monkeypatch):
    import agentforge.agents as module
    reply = {"message": {"role": "assistant", "content":
             '{"name":"write_file","arguments":{"path":"sample.py","content":"value = 2"}}'}}
    monkeypatch.setattr(module, "_ollama_chat", lambda endpoint, payload: reply)
    result = asyncio.run(run_agent("Developer", "instructions", "Fix value",
                                   tools=[Workspace(tmp_path)], provider="ollama"))
    assert "Repeated write suppressed" in result
    assert (tmp_path / "sample.py").read_text() == "value = 2"


def test_ollama_exact_replacement_tool(tmp_path, monkeypatch):
    import agentforge.agents as module
    (tmp_path / "sample.py").write_text("value = 0\n")
    replies = [
        {"message": {"role": "assistant", "content":
                     '{"name":"replace_text","arguments":{"path":"sample.py","old":"value = 0","new":"value = 2"}}'}},
        {"message": {"role": "assistant", "content": "Fixed."}},
    ]
    monkeypatch.setattr(module, "_ollama_chat", lambda endpoint, payload: replies.pop(0))
    result = asyncio.run(run_agent("Developer", "instructions", "Fix value",
                                   tools=[Workspace(tmp_path)], provider="ollama"))
    assert result == "Fixed."
    assert (tmp_path / "sample.py").read_text() == "value = 2\n"


def test_ollama_can_edit_after_eight_reads(tmp_path, monkeypatch):
    import agentforge.agents as module
    (tmp_path / "sample.py").write_text("value = 0\n")
    calls = 0

    def chat(endpoint, payload):
        nonlocal calls
        calls += 1
        if calls <= 9:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_files", "arguments": {"query": "value", "path": "sample.py"}}}
            ]}}
        if calls == 10:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "replace_text", "arguments": {
                    "path": "sample.py", "old": "value = 0", "new": "value = 2"}}}
            ]}}
        return {"message": {"role": "assistant", "content": "Fixed."}}

    monkeypatch.setattr(module, "_ollama_chat", chat)
    assert asyncio.run(run_agent("Developer", "instructions", "Fix value", tools=[Workspace(tmp_path)],
                                 provider="ollama")) == "Fixed."
    assert (tmp_path / "sample.py").read_text() == "value = 2\n"

