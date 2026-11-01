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
    observed = []
    replies = [
        {"message": {"role": "assistant", "content":
                     '```json\n{"name":"write_file","arguments":{"path":"sample.py","content":"def value(): return 2"}}\n```'}},
        {"message": {"role": "assistant", "content": "Done."}},
    ]
    def chat(endpoint, payload):
        import copy
        observed.append(copy.deepcopy(payload))
        return replies.pop(0)

    monkeypatch.setattr(module, "_ollama_chat", chat)
    result = asyncio.run(run_agent("Developer", "instructions", "Fix value",
                                   tools=[Workspace(tmp_path)], provider="ollama"))
    assert result == "Done."
    assert "return 2" in (tmp_path / "sample.py").read_text()
    assert observed[1]["messages"][-2]["content"] == ""
    assert observed[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "write_file"


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


def test_ollama_numbered_line_replacement(tmp_path, monkeypatch):
    import agentforge.agents as module
    (tmp_path / "sample.py").write_text("def value():\n    return 0\n")
    replies = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "replace_line", "arguments": {
                "path": "sample.py", "line": 2, "new": "return 2"}}}
        ]}},
        {"message": {"role": "assistant", "content": "Fixed."}},
    ]
    monkeypatch.setattr(module, "_ollama_chat", lambda endpoint, payload: replies.pop(0))
    assert asyncio.run(run_agent("Developer", "instructions", "Fix value", tools=[Workspace(tmp_path)],
                                 provider="ollama")) == "Fixed."
    assert (tmp_path / "sample.py").read_text() == "def value():\n    return 2\n"



def _tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def _reply(*calls):
    return {"message": {"role": "assistant", "content": "", "tool_calls": list(calls)}}


def _run_replies(tmp_path, monkeypatch, replies):
    import agentforge.agents as module
    iterator = iter(replies)
    monkeypatch.setattr(module, "_ollama_chat", lambda endpoint, payload: next(iterator))
    return asyncio.run(run_agent("Developer", "instructions", "Fix files",
                                 tools=[Workspace(tmp_path)], provider="ollama"))


def test_whitespace_change_is_applied(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("    value = 2\n")
    _run_replies(tmp_path, monkeypatch, [
        _reply(_tool("write_file", path="code.py", content="value = 2\n")),
        {"message": {"content": "Done"}},
    ])
    assert (tmp_path / "code.py").read_text() == "value = 2\n"


def test_duplicate_does_not_discard_later_edit_in_batch(tmp_path, monkeypatch):
    first = _tool("write_file", path="first.py", content="value = 2\n")
    _run_replies(tmp_path, monkeypatch, [
        _reply(first),
        _reply(first, _tool("write_file", path="second.py", content="value = 3\n")),
        {"message": {"content": "Done"}},
    ])
    assert (tmp_path / "second.py").read_text() == "value = 3\n"


def test_repeated_call_reapplies_after_intervening_edit(tmp_path, monkeypatch):
    first = _tool("write_file", path="code.py", content="value = 2\n")
    _run_replies(tmp_path, monkeypatch, [
        _reply(first),
        _reply(_tool("write_file", path="code.py", content="value = 3\n")),
        _reply(first),
        {"message": {"content": "Done"}},
    ])
    assert (tmp_path / "code.py").read_text() == "value = 2\n"


def test_invalid_tool_argument_allows_model_to_recover(tmp_path, monkeypatch):
    _run_replies(tmp_path, monkeypatch, [
        _reply(_tool("write_file", path=123, content="value = 0")),
        _reply(_tool("write_file", path="code.py", content="value = 2\n")),
        {"message": {"content": "Done"}},
    ])
    assert (tmp_path / "code.py").read_text() == "value = 2\n"


def test_invalid_ollama_envelope_fails_explicitly(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(RuntimeError, match="invalid message envelope"):
        _run_replies(tmp_path, monkeypatch, [{"error": "model unavailable"}])


def test_tool_batch_limit(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(RuntimeError, match="excessive tool calls"):
        _run_replies(tmp_path, monkeypatch, [_reply(*([_tool("list_files")] * 33))])


def test_ollama_ignores_environment_proxy_and_closes_response(monkeypatch):
    import io
    import json
    import agentforge.agents as module
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:9999")
    response = io.BytesIO(json.dumps({"message": {"content": "Done"}}).encode())

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/api/chat"
            assert timeout == 180
            return response

    def build(handler):
        assert handler.proxies == {}
        return Opener()

    monkeypatch.setattr(module, "build_opener", build)
    assert module._ollama_chat("http://127.0.0.1:11434/api/chat", {})["message"]["content"] == "Done"
    assert response.closed


def test_ollama_endpoint_rejects_credentials_and_query(tmp_path, monkeypatch):
    import pytest
    for base in ["http://user:secret@localhost:11434/v1/", "http://localhost:11434/v1/?x=1"]:
        with pytest.raises(ValueError, match="local"):
            asyncio.run(run_agent("Developer", "", "", provider="ollama", ollama_base_url=base))


def test_fenced_tool_request_in_explanation_executes_first_call(tmp_path, monkeypatch):
    import copy
    import agentforge.agents as module
    (tmp_path / "sample.py").write_text("value = 0\n")
    replies = [
        {"message": {"role": "assistant", "content":
             "Inspect:\n```json\n"
             '{"name":"search_files","arguments":{"query":"value","path":"sample.py"}}'
             "\n```\nThen edit:\n```json\n"
             '{"name":"replace_line","arguments":{"path":"sample.py","line":1,"new":"value = 2"}}'
             "\n```"}},
        {"message": {"role": "assistant", "content":
             '{"name":"replace_line","arguments":{"path":"sample.py","line":1,"new":"value = 2"}}'}},
        {"message": {"role": "assistant", "content": "Fixed."}},
    ]
    seen = []

    def chat(endpoint, payload):
        seen.append(copy.deepcopy(payload))
        return replies.pop(0)

    monkeypatch.setattr(module, "_ollama_chat", chat)
    assert asyncio.run(run_agent("Developer", "instructions", "Fix value", tools=[Workspace(tmp_path)],
                                 provider="ollama")) == "Fixed."
    assert seen[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "search_files"
    assert (tmp_path / "sample.py").read_text() == "value = 2\n"

