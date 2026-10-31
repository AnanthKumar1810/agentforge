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
