"""HTTP API for AgentForge runs."""
from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from agentforge.engine import AgentForgeEngine
from agentforge.models import RunReport

app = FastAPI(title="AgentForge", version="0.1.0", description="Controlled multi-agent software engineering prototype")

class RunRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    use_crewai: bool = False
    use_beeai: bool = False
    max_repair_rounds: int | None = Field(default=None, ge=0, le=5)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "agentforge"}

@app.post("/runs", response_model=RunReport)
async def create_run(payload: RunRequest) -> RunReport:
    try:
        return await AgentForgeEngine().run(payload.request, use_crewai=payload.use_crewai, use_beeai=payload.use_beeai, max_repair_rounds=payload.max_repair_rounds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Run failed: {type(exc).__name__}: {exc}") from exc
