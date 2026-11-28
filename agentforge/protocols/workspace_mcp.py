"""Workspace MCP tool server for AgntID runtime proxying.

Exposes AgentForge's 7 ``Workspace``-confined coding tools over MCP Streamable HTTP
so an AgntID Runtime (``achr-runtime``) can proxy, shortlist, authorize, and audit
coding-agent tool calls against a designated workspace directory.

Independent AgentForge workspace security controls (path containment, credential
exclusions, maintained-test protections, symlink checks, and size limits) remain
active on every tool call even after AgntID grants authorization.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from agentforge.config import Settings
from agentforge.workspace import Workspace, WorkspaceError
from agentforge.workspace_routes import WorkspaceRoutes, workspace_data_root


def create_workspace_mcp_server(
    workspace_root: Path, *, max_file_bytes: int | None = None, routed: bool = False,
):
    """Create a FastMCP server exposing Workspace-confined coding tools."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:
        raise RuntimeError("MCP is optional; install with pip install -e '.[all]'") from exc

    limit = max_file_bytes if max_file_bytes is not None else Settings.from_env().max_file_bytes
    routes = WorkspaceRoutes(workspace_root) if routed else None
    fixed_workspace = None if routed else Workspace(workspace_root, max_file_bytes=limit)
    security = TransportSecuritySettings(
        allowed_hosts=["localhost:*", "127.0.0.1:*", "[::1]:*", "host.docker.internal:*"],
    ) if routed else None
    server = FastMCP("AgentForgeWorkspace", transport_security=security)

    def perform(workspace_id: str, action: Callable[[Workspace], str]) -> str | dict:
        if routes is None:
            if workspace_id or fixed_workspace is None:
                return "ERROR: This server does not accept workspace routes"
            try:
                return action(fixed_workspace)
            except (WorkspaceError, OSError, UnicodeError) as exc:
                return f"ERROR: {exc}"

        try:
            workspace = routes.resolve(workspace_id, max_file_bytes=limit)
        except WorkspaceError:
            return {"agentforge_workspace_result": {"error": "route_unavailable"}}
        try:
            result = action(workspace)
        except (WorkspaceError, OSError, UnicodeError):
            return {"agentforge_workspace_result": {"error": "workspace_action_failed"}}
        return {"agentforge_workspace_result": {"value": result}}

    @server.tool()
    def read_file(path: str, workspace_id: str = "") -> str | dict:
        """Read a UTF-8 text file from this run's workspace."""
        return perform(workspace_id, lambda workspace: workspace.read_text(path))

    @server.tool()
    def read_lines(path: str, start_line: int, end_line: int, workspace_id: str = "") -> str | dict:
        """Read a 1-based inclusive line range, up to 200 lines."""
        return perform(workspace_id, lambda workspace: workspace.read_lines(path, start_line, end_line))

    @server.tool()
    def search_files(query: str, path: str = ".", workspace_id: str = "") -> str | dict:
        """Find literal text in workspace Python files and return path and line numbers."""
        return perform(workspace_id, lambda workspace: workspace.search_files(query, path))

    @server.tool()
    def write_file(path: str, content: str, workspace_id: str = "") -> str | dict:
        """Create or replace a UTF-8 file within this run's workspace."""
        return perform(workspace_id, lambda workspace: workspace.write_text(path, content))

    @server.tool()
    def replace_text(path: str, old: str, new: str, workspace_id: str = "") -> str | dict:
        """Replace exactly one matching text fragment in a workspace file."""
        return perform(workspace_id, lambda workspace: workspace.replace_text(path, old, new))

    @server.tool()
    def replace_line(path: str, line: int, new: str, workspace_id: str = "") -> str | dict:
        """Replace a numbered source line, using the line number from search_files."""
        return perform(workspace_id, lambda workspace: workspace.replace_line(path, line, new))

    @server.tool()
    def list_files(path: str = ".", workspace_id: str = "") -> str | dict:
        """List files within this run's workspace."""
        return perform(workspace_id, lambda workspace: "\n".join(workspace.list_files(path)) or "(workspace is empty)")

    return server


def run_workspace_mcp_server(workspace_root: Path | None = None, *, routed: bool = False) -> None:
    """Start the Workspace MCP server bound to localhost by default."""
    root = workspace_root or Path(
        os.getenv("AGENTFORGE_MCP_WORKSPACE", str(Settings.from_env().data_dir / "mcp-workspace"))
    )
    if routed:
        root = workspace_root or workspace_data_root(Settings.from_env().data_dir)
    server = create_workspace_mcp_server(root, routed=routed)
    server.settings.host = os.getenv("AGENTFORGE_WORKSPACE_MCP_HOST", "127.0.0.1")
    server.settings.port = int(os.getenv("AGENTFORGE_WORKSPACE_MCP_PORT", "8003"))
    server.run(transport="streamable-http")


if __name__ == "__main__":
    run_workspace_mcp_server()
