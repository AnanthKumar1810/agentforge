"""A2A-style JSON-RPC facade. Verify schema compatibility with the target A2A SDK/client.

This deliberately small facade is a prototype, not a claim of full A2A conformance.
"""
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from agentforge.engine import AgentForgeEngine

app = FastAPI(title="AgentForge A2A facade", version="0.1.0")

@app.get("/.well-known/agent-card.json")
async def agent_card() -> dict:
    return {
        "name": "AgentForge",
        "description": "Plans, implements, verifies, repairs, and reviews software tasks.",
        "url": "http://127.0.0.1:8002/", "version": "0.1.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"], "defaultOutputModes": ["text"],
        "skills": [{"id": "software-build", "name": "Implement and verify a software task", "tags": ["coding", "testing"]}],
    }

@app.post("/")
async def json_rpc(request: Request):
    body = await request.json(); rpc_id = body.get("id"); method = body.get("method"); params = body.get("params") or {}
    if method not in {"message/send", "tasks/send"}:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Unsupported method: {method}"}})
    task_text = params.get("request") or params.get("text")
    if not task_text:
        parts = ((params.get("message") or {}).get("parts") or [])
        task_text = "\n".join(str(p.get("text", "")) for p in parts if p.get("kind", "text") == "text").strip()
    if not task_text:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Provide params.request or text message parts"}})
    try:
        report = await AgentForgeEngine().run(str(task_text))
    except Exception as exc:
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}})
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {
        "id": report.run_id, "status": {"state": "completed" if report.status == "passed" else "failed"},
        "artifacts": [{"name": "report", "parts": [{"kind": "text", "text": f"Status: {report.status}\nReview: {report.review}\nJSON report: {report.artifacts.get('json', '')}"}]}],
    }}
