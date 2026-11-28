"""Command-line entry point."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys

from agentforge.engine import AgentForgeEngine


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentforge", description="Build, verify, repair, and review a software task")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="run the agent workflow")
    build.add_argument("request")
    build.add_argument("--source-repo", help="local repository path")
    build.add_argument("--test-manifest", help="JSON test selection manifest")
    build.add_argument("--generate-tests", action="store_true")
    build.add_argument("--allow-local-execution", action="store_true", help="run repository checks; use only with trusted code or OS isolation")
    build.add_argument("--crewai", action="store_true")
    build.add_argument("--beeai", action="store_true")
    build.add_argument("--max-repair-rounds", type=int, choices=range(0, 6))
    build.add_argument("--agntid-mode", choices=["off", "mcp", "mock"], help="runtime authorization mode")
    api = sub.add_parser("api", help="start the FastAPI service")
    api.add_argument("--host", default="127.0.0.1"); api.add_argument("--port", type=int, default=8000)
    a2a = sub.add_parser("a2a", help="start the A2A-style JSON-RPC facade")
    a2a.add_argument("--host", default="127.0.0.1"); a2a.add_argument("--port", type=int, default=8002)
    mcp = sub.add_parser("mcp", help="start the MCP server")
    mcp.add_argument("--host", default="127.0.0.1"); mcp.add_argument("--port", type=int, default=8001)
    wmcp = sub.add_parser("workspace-mcp", help="start the Workspace MCP tool server for AgntID proxying")
    wmcp.add_argument("--workspace", help="workspace directory root")
    wmcp.add_argument("--routed", action="store_true", help="serve isolated per-run workspaces")
    wmcp.add_argument("--host", default="127.0.0.1"); wmcp.add_argument("--port", type=int, default=8003)
    return parser


async def run_build(args: argparse.Namespace) -> int:
    report = await AgentForgeEngine().run(args.request, use_crewai=args.crewai, use_beeai=args.beeai, max_repair_rounds=args.max_repair_rounds, source_repo=args.source_repo, test_manifest=args.test_manifest, generate_tests=args.generate_tests, allow_local_execution=args.allow_local_execution, agntid_mode=args.agntid_mode)
    print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if report.status == "passed" else 1


def main() -> None:
    args = make_parser().parse_args()
    try:
        if args.command == "build":
            raise SystemExit(asyncio.run(run_build(args)))
        if args.command == "api":
            import uvicorn
            uvicorn.run("agentforge.api:app", host=args.host, port=args.port, reload=False); return
        if args.command == "a2a":
            import uvicorn
            uvicorn.run("agentforge.protocols.a2a_server:app", host=args.host, port=args.port); return
        if args.command == "mcp":
            os.environ["AGENTFORGE_MCP_HOST"] = args.host; os.environ["AGENTFORGE_MCP_PORT"] = str(args.port)
            from agentforge.protocols.mcp_server import run_server
            run_server(); return
        if args.command == "workspace-mcp":
            from pathlib import Path
            os.environ["AGENTFORGE_WORKSPACE_MCP_HOST"] = args.host
            os.environ["AGENTFORGE_WORKSPACE_MCP_PORT"] = str(args.port)
            from agentforge.protocols.workspace_mcp import run_workspace_mcp_server
            run_workspace_mcp_server(Path(args.workspace) if args.workspace else None, routed=args.routed)
            return
    except KeyboardInterrupt:
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"AgentForge error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
