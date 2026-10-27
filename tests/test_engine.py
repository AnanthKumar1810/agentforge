from pathlib import Path
import pytest
from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine

async def fake_agent(name, instructions, prompt, *, tools=None, model="unused"):
    if name == "Supervisor": return "Use a minimal example; syntax must pass."
    if name == "Developer": return "Workspace unchanged for this orchestration test."
    if name == "Reviewer": return "Review based on supplied deterministic evidence."
    if name == "Tester": return "Inspect the earliest failing check."
    raise AssertionError(f"Unexpected role: {name}")

@pytest.mark.asyncio
async def test_engine_writes_reports(tmp_path: Path, monkeypatch) -> None:
    # Avoid requiring the external Agents SDK for this orchestration/report test.
    import agentforge.engine as engine_module
    monkeypatch.setattr(engine_module, "build_workspace_tools", lambda workspace: [])
    settings = Settings("test-model", tmp_path / "data", 1, 10, 1000, "unused")
    report = await AgentForgeEngine(settings, agent_call=fake_agent).run("Test orchestration")
    assert report.status == "passed"
    assert Path(report.artifacts["json"]).is_file()
    assert Path(report.artifacts["markdown"]).is_file()
