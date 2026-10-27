"""OpenAI Agents SDK adapter and workspace-scoped Developer tools."""

from __future__ import annotations
from typing import Any

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
    def write_file(path: str, content: str) -> str:
        """Create or replace a UTF-8 file within this run's workspace."""
        try:
            return workspace.write_text(path, content)
        except (WorkspaceError, OSError, UnicodeError) as exc:
            return f"ERROR: {exc}"

    @function_tool
    def list_files(path: str = ".") -> str:
        """List files within this run's workspace."""
        try:
            return "\n".join(workspace.list_files(path)) or "(workspace is empty)"
        except (WorkspaceError, OSError) as exc:
            return f"ERROR: {exc}"

    return [read_file, write_file, list_files]


async def run_agent(
    name: str, instructions: str, prompt: str, *, tools: list[Any] | None = None,
    model: str = "gpt-5-mini",
) -> str:
    require_openai_key()
    try:
        from agents import Agent, Runner
    except ImportError as exc:
        raise RuntimeError("OpenAI Agents SDK is not installed; install project dependencies") from exc
    agent = Agent(name=name, instructions=instructions, model=model, tools=tools or [])
    result = await Runner.run(agent, prompt)
    output = getattr(result, "final_output", None)
    return output if isinstance(output, str) else str(output if output is not None else result)
