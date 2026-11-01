from fastapi.testclient import TestClient
from agentforge.api import app


def test_api_requires_token(monkeypatch):
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    monkeypatch.delenv("AGENTFORGE_API_TOKEN", raising=False)
    assert client.post("/runs", json={"request": "Example"}).status_code == 503
    monkeypatch.setenv("AGENTFORGE_API_TOKEN", "example-secret")
    assert client.post("/runs", json={"request": "Example"}).status_code == 401
    auth = {"Authorization": "Bearer example-secret"}
    assert client.get("/runs/not-a-run", headers=auth).status_code == 422


def test_dashboard_submits_and_displays_job_result(monkeypatch, tmp_path):
    import time
    from datetime import datetime, timezone
    import agentforge.api as api
    from agentforge.models import RunReport, ValidationSummary
    monkeypatch.setenv("AGENTFORGE_API_TOKEN", "demo-token")
    monkeypatch.setenv("AGENTFORGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTFORGE_PROVIDER", "ollama")
    class FakeEngine:
        async def run(self, request, **kwargs):
            assert request == "Fix sample.value()"
            assert kwargs["source_repo"] == "examples/repair_target"
            assert kwargs["allow_local_execution"] is True
            now = datetime.now(timezone.utc)
            return RunReport(run_id="a" * 32, request=request, status="passed",
                             started_at=now, finished_at=now, workspace=str(tmp_path),
                             validation=ValidationSummary(passed=True, status="verified"))
    monkeypatch.setattr(api, "AgentForgeEngine", FakeEngine)
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200 and "Start repair" in page.text
    headers = {"Authorization": "Bearer demo-token"}
    assert client.get("/configuration", headers=headers).json()["provider"] == "ollama"
    assert client.post("/jobs", json={"request": "Fix sample.value()"}).status_code == 401
    submitted = client.post("/jobs", headers=headers, json={
        "request": "Fix sample.value()", "source_repo": "examples/repair_target",
        "allow_local_execution": True})
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    for _ in range(100):
        result = client.get(f"/jobs/{job_id}", headers=headers).json()
        if result["status"] == "completed":
            break
        time.sleep(.01)
    assert result["status"] == "completed"
    assert result["report"]["validation"]["status"] == "verified"
