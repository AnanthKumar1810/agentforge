"""MCP task/report tools using the Python MCP SDK streamable HTTP transport."""
from __future__ import annotations
import asyncio
import os
from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP is optional; install with pip install -e '.[all]'") from exc
    server = FastMCP("AgentForge")

    @server.tool()
    def submit_software_task(request: str, use_crewai: bool = False, use_beeai: bool = False) -> str:
        """Submit a software task and return the structured run report."""
        return asyncio.run(AgentForgeEngine().run(request, use_crewai=use_crewai, use_beeai=use_beeai)).model_dump_json(indent=2)

    @server.tool()
    def read_run_report(run_id: str) -> str:
        """Read report.json for a known run ID; this tool never reads workspace source files."""
        if not run_id or any(char not in "0123456789abcdef" for char in run_id.lower()):
            return "ERROR: invalid run ID"
        base = (Settings.from_env().data_dir / "runs").resolve()
        target = (base / run_id / "artifacts" / "report.json").resolve()
        try:
            target.relative_to(base)
        except ValueError:
            return "ERROR: invalid report path"
        return target.read_text(encoding="utf-8") if target.is_file() else "ERROR: report not found"
    return server


def run_server() -> None:
    server = create_server()
    server.settings.host = os.getenv("AGENTFORGE_MCP_HOST", "127.0.0.1")
    server.settings.port = int(os.getenv("AGENTFORGE_MCP_PORT", "8001"))
    server.run(transport="streamable-http")

if __name__ == "__main__":
    run_server()
