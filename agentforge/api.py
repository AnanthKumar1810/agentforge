"""HTTP API for AgentForge runs."""
from __future__ import annotations
import os
import re
import secrets
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from agentforge.engine import AgentForgeEngine
from agentforge.config import Settings
from agentforge.models import RunReport

app = FastAPI(title="AgentForge", version="0.1.0", description="Controlled multi-agent software engineering prototype")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentforge-job")
_jobs: dict[str, dict] = {}
_job_lock = Lock()


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")

class RunRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    source_repo: str | None = None
    test_manifest: str | None = None
    generate_tests: bool = False
    allow_local_execution: bool = False
    use_crewai: bool = False
    use_beeai: bool = False
    max_repair_rounds: int | None = Field(default=None, ge=0, le=5)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "agentforge"}


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("AGENTFORGE_API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="API token is not configured")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid API token")


@app.get("/configuration", dependencies=[Depends(require_token)])
def configuration() -> dict:
    settings = Settings.from_env()
    return {"provider": settings.provider, "model": settings.model,
            "validator": settings.validator}


def _run_job(job_id: str, payload: RunRequest) -> None:
    with _job_lock:
        _jobs[job_id]["status"] = "running"
    try:
        report = asyncio.run(AgentForgeEngine().run(
            payload.request, use_crewai=payload.use_crewai, use_beeai=payload.use_beeai,
            max_repair_rounds=payload.max_repair_rounds, source_repo=payload.source_repo,
            test_manifest=payload.test_manifest, generate_tests=payload.generate_tests,
            allow_local_execution=payload.allow_local_execution))
        with _job_lock:
            _jobs[job_id] = {"status": "completed", "report": report.model_dump(mode="json")}
    except Exception as exc:
        with _job_lock:
            _jobs[job_id] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


@app.post("/jobs", status_code=202, dependencies=[Depends(require_token)])
def submit_job(payload: RunRequest) -> dict[str, str]:
    import uuid
    job_id = uuid.uuid4().hex
    with _job_lock:
        _jobs[job_id] = {"status": "queued"}
    _executor.submit(_run_job, job_id, payload)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def job_status(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=422, detail="Invalid job ID")
    with _job_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found (jobs are held in server memory)")
        return dict(job)


@app.post("/runs", response_model=RunReport, dependencies=[Depends(require_token)])
async def create_run(payload: RunRequest) -> RunReport:
    try:
        return await AgentForgeEngine().run(payload.request, use_crewai=payload.use_crewai, use_beeai=payload.use_beeai, max_repair_rounds=payload.max_repair_rounds, source_repo=payload.source_repo, test_manifest=payload.test_manifest, generate_tests=payload.generate_tests, allow_local_execution=payload.allow_local_execution)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Run failed: {type(exc).__name__}: {exc}") from exc


@app.get("/runs/{run_id}", response_model=RunReport, dependencies=[Depends(require_token)])
async def get_run(run_id: str) -> RunReport:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise HTTPException(status_code=422, detail="Invalid run ID")
    path = (Settings.from_env().data_dir / "runs" / run_id / "artifacts" / "report.json").resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Run not found")
    return RunReport.model_validate_json(path.read_text(encoding="utf-8"))
