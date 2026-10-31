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
    def list_files(path: str = ".") -> str:
        """List files within this run's workspace."""
        try:
            return "\n".join(workspace.list_files(path)) or "(workspace is empty)"
        except (WorkspaceError, OSError) as exc:
            return f"ERROR: {exc}"

    return [read_file, read_lines, search_files, write_file, replace_text, list_files]


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
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path != "/api/chat":
        raise ValueError("Ollama endpoint must be local")
    if tools:
        instructions += ("\nUse search_files and read_lines to inspect large files. Prefer replace_text for small edits. "
                         "When using a file tool, send a native tool call. If your model cannot send one, "
                         "respond with ONLY a JSON object such as "
                         '{"name":"write_file","arguments":{"path":"sample.py","content":"..."}}. '
                         "Wait for the tool result before claiming a change.")
    messages: list[dict] = [{"role": "system", "content": instructions},
                            {"role": "user", "content": prompt}]
    workspace = tools[0] if tools and isinstance(tools[0], Workspace) else None
    applied_writes: set[tuple[str, str]] = set()
    tool_trace: list[str] = []
    for _ in range(16):
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.1, "num_predict": 1200}}
        if workspace is not None:
            payload["tools"] = _OLLAMA_TOOLS
        response = await asyncio.to_thread(_ollama_chat, endpoint, payload)
        message = response.get("message", {})
        calls = message.get("tool_calls") or []
        if not calls and workspace is not None:
            content = str(message.get("content", "")).strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                candidate = json.loads(content)
                if isinstance(candidate, dict) and candidate.get("name") in {"read_file", "read_lines", "search_files", "write_file", "replace_text", "list_files"}:
                    calls = [{"function": candidate}]
            except json.JSONDecodeError:
                pass
        if not calls:
            return str(message.get("content", ""))
        if workspace is None:
            raise RuntimeError("Ollama requested tools for an agent without a workspace")
        messages.append(message)
        for call in calls:
            function = call.get("function", {})
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
                if name == "read_file":
                    result = workspace.read_text(args["path"])
                elif name == "read_lines":
                    result = workspace.read_lines(args["path"], args["start_line"], args["end_line"])
                elif name == "search_files":
                    result = workspace.search_files(args["query"], args.get("path", "."))
                elif name == "write_file":
                    fingerprint = (args["path"], args["content"])
                    if fingerprint in applied_writes:
                        return "Repeated write suppressed; the change was applied in the workspace."
                    try:
                        if workspace.read_text(args["path"]).strip() == args["content"].strip():
                            return "Equivalent write suppressed; the change is already in the workspace."
                    except WorkspaceError:
                        pass
                    result = workspace.write_text(args["path"], args["content"])
                    applied_writes.add(fingerprint)
                elif name == "replace_text":
                    fingerprint = (args["path"], args["old"] + "\0" + args["new"])
                    if fingerprint in applied_writes:
                        return "Repeated replacement suppressed; the change was applied in the workspace."
                    result = workspace.replace_text(args["path"], args["old"], args["new"])
                    applied_writes.add(fingerprint)
                elif name == "list_files":
                    result = "\n".join(workspace.list_files(args.get("path", ".")))
                else:
                    result = "ERROR: Unknown tool"
            except (WorkspaceError, OSError, UnicodeError, KeyError, TypeError) as exc:
                result = f"ERROR: {exc}"
            messages.append({"role": "tool", "tool_name": name, "content": result[:500_000]})
    if applied_writes:
        return f"Applied {len(applied_writes)} file write(s); model reached the tool-turn limit. Run deterministic validation."
    raise RuntimeError("Ollama agent exceeded the tool-turn limit without editing files; "
                       "tool trace: " + ", ".join(tool_trace[:32]))

