"""OpenAI Agents SDK adapter and workspace-scoped Developer tools."""

from __future__ import annotations
import asyncio
import json
from typing import Any
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlsplit

from agentforge.config import require_openai_key
from agentforge.workspace import Workspace, WorkspaceError


def build_workspace_tools(workspace: Workspace) -> list[Any]:
    try:
        from agents import function_tool
    except ImportError as exc:
        raise RuntimeError("Install the OpenAI Agents SDK with pip install -e '.[dev]'") from exc

    @function_tool
    def read_file(path: str) -> str:
        """Read a UTF-8 text file from this run's workspace."""
        try:
            return workspace.read_text(path)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def read_lines(path: str, start_line: int, end_line: int) -> str:
        """Read a 1-based inclusive line range, up to 200 lines."""
        try:
            return workspace.read_lines(path, start_line, end_line)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def search_files(query: str, path: str = ".") -> str:
        """Find literal text in workspace Python files and return path and line numbers."""
        try:
            return workspace.search_files(query, path)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Create or replace a UTF-8 file within this run's workspace."""
        try:
            return workspace.write_text(path, content)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def replace_text(path: str, old: str, new: str) -> str:
        """Replace exactly one matching text fragment in a workspace file."""
        try:
            return workspace.replace_text(path, old, new)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def replace_line(path: str, line: int, new: str) -> str:
        """Replace a numbered source line, using the line number from search_files."""
        try:
            return workspace.replace_line(path, line, new)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def list_files(path: str = ".") -> str:
        """List files within this run's workspace."""
        try:
            return "\n".join(workspace.list_files(path)) or "(workspace is empty)"
        except (WorkspaceError, OSError) as exc:
            return f"ERROR: {exc}"

    return [read_file, read_lines, search_files, write_file, replace_text, replace_line, list_files]


async def run_agent(
    name: str, instructions: str, prompt: str, *, tools: list[Any] | None = None,
    model: str = "gpt-5-mini", provider: str = "openai",
    ollama_base_url: str = "http://127.0.0.1:11434/v1/",
) -> str:
    if provider == "ollama":
        return await _run_ollama_agent(instructions, prompt, tools or [], model, ollama_base_url)
    if provider == "openai":
        require_openai_key()
    try:
        from agents import Agent, Runner
    except ImportError as exc:
        raise RuntimeError("OpenAI Agents SDK is not installed; install project dependencies") from exc
    agent = Agent(name=name, instructions=instructions, model=model, tools=tools or [])
    result = await Runner.run(agent, prompt)
    output = getattr(result, "final_output", None)
    return output if isinstance(output, str) else str(output if output is not None else result)


_OLLAMA_TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 workspace file.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or replace a workspace file.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "List workspace files.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "search_files", "description": "Search Python files for literal text and return line numbers.",
      "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}},
                     "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_lines", "description": "Read a 1-based inclusive line range, at most 200 lines.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                                             "end_line": {"type": "integer"}},
                     "required": ["path", "start_line", "end_line"]}}},
    {"type": "function", "function": {"name": "replace_text", "description": "Replace one exact text fragment in a file without rewriting the whole file.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                             "new": {"type": "string"}},
                     "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "replace_line", "description": "Replace one source line by its 1-based line number from search_files. Preserve existing indentation when new has none.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer"},
                                             "new": {"type": "string"}},
                     "required": ["path", "line", "new"]}}},
]


def _ollama_chat(endpoint: str, payload: dict) -> dict:
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    # The configured endpoint is validated as localhost by Settings.
    with build_opener(ProxyHandler({})).open(request, timeout=180) as response:
        return json.load(response)


async def _run_ollama_agent(instructions: str, prompt: str, tools: list[Any],
                            model: str, base_url: str) -> str:
    endpoint = base_url.rstrip("/").removesuffix("/v1") + "/api/chat"
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path != "/api/chat" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama endpoint must be local")
    if tools:
        instructions += ("\nUse search_files and read_lines to inspect large files. Use replace_line with a search result's line number for one-line edits. "
                         "When using a file tool, send a native tool call. If your model cannot send one, "
                         "respond with ONLY a JSON object such as "
                         '{"name":"write_file","arguments":{"path":"sample.py","content":"..."}}. '
                         "Wait for the tool result before claiming a change.")
    messages: list[dict] = [{"role": "system", "content": instructions},
                            {"role": "user", "content": prompt}]
    workspace = tools[0] if tools and isinstance(tools[0], Workspace) else None
    applied_writes: dict[str, str] = {}
    tool_trace: list[str] = []
    for _ in range(16):
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.1, "num_predict": 1200}}
        if workspace is not None:
            payload["tools"] = _OLLAMA_TOOLS
        response = await asyncio.to_thread(_ollama_chat, endpoint, payload)
        if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
            raise RuntimeError("Ollama returned an invalid message envelope")
        message = response["message"]
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 32:
            raise RuntimeError("Ollama returned invalid or excessive tool calls")
        if not calls and workspace is not None:
            content = str(message.get("content", "")).strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                candidate = json.loads(content)
                if isinstance(candidate, dict) and candidate.get("name") in {"read_file", "read_lines", "search_files", "write_file", "replace_text", "replace_line", "list_files"}:
                    calls = [{"function": candidate}]
            except json.JSONDecodeError:
                pass
        if not calls:
            return str(message.get("content", ""))
        if workspace is None:
            raise RuntimeError("Ollama requested tools for an agent without a workspace")
        messages.append(message)
        batch_changed = False
        batch_write = False
        batch_error = False
        for call in calls:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            if not isinstance(function, dict):
                function = {}
            name = function.get("name")
            args = function.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            tool_trace.append(f"{name}({str(args.get('path', ''))[:80]})")
            try:
                schema = next((t["function"]["parameters"] for t in _OLLAMA_TOOLS
                               if t["function"]["name"] == name), None)
                if schema is None:
                    raise WorkspaceError("Unknown tool")
                if any(key not in args for key in schema.get("required", [])):
                    raise WorkspaceError("Missing required tool argument")
                for key, value in args.items():
                    spec = schema["properties"].get(key)
                    if spec is None:
                        raise WorkspaceError("Unknown tool argument")
                    expected = str if spec["type"] == "string" else int
                    if not isinstance(value, expected) or isinstance(value, bool):
                        raise WorkspaceError(f"Invalid type for tool argument: {key}")
                if name == "read_file":
                    result = workspace.read_text(args["path"])
                elif name == "read_lines":
                    result = workspace.read_lines(args["path"], args["start_line"], args["end_line"])
                elif name == "search_files":
                    result = workspace.search_files(args["query"], args.get("path", "."))
                elif name in {"write_file", "replace_text", "replace_line"}:
                    batch_write = True
                    fingerprint = json.dumps([name, args], sort_keys=True)
                    try:
                        before = workspace.read_text(args["path"])
                    except WorkspaceError:
                        before = None
                    if fingerprint in applied_writes and before == applied_writes[fingerprint]:
                        result = "Repeated write suppressed; current file matches the applied change."
                    elif name == "write_file" and before == args["content"]:
                        result = "Equivalent write suppressed; exact content is already present."
                    else:
                        if name == "write_file":
                            result = workspace.write_text(args["path"], args["content"])
                        elif name == "replace_text":
                            result = workspace.replace_text(args["path"], args["old"], args["new"])
                        else:
                            result = workspace.replace_line(args["path"], args["line"], args["new"])
                        after = workspace.read_text(args["path"])
                        applied_writes[fingerprint] = after
                        batch_changed |= before != after
                elif name == "list_files":
                    result = "\n".join(workspace.list_files(args.get("path", ".")))
                else:
                    result = "ERROR: Unknown tool"
            except (WorkspaceError, OSError, UnicodeError, KeyError, TypeError) as exc:
                result = f"ERROR: {exc}"
                batch_error = True
            tool_trace[-1] += ": " + (result[:120] if result.startswith("ERROR:") else "ok")
            messages.append({"role": "tool", "tool_name": name, "content": result[:500_000]})
        # Finish the entire tool batch before handing control back to validation.
        if batch_write and not batch_changed and not batch_error:
            return "Repeated write suppressed; workspace is unchanged by this tool batch. Run deterministic validation."
    if applied_writes:
        return f"Applied {len(applied_writes)} file write(s); model reached the tool-turn limit. Run deterministic validation."
    raise RuntimeError("Ollama agent exceeded the tool-turn limit without editing files; "
                       "tool trace: " + ", ".join(tool_trace[:32]))

