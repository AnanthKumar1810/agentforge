from pathlib import Path
import json
from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine

async def fake_agent(name, instructions, prompt, *, tools=None, model="unused"):
    if name == "Supervisor": return "Use a minimal example; syntax must pass."
    if name == "Developer": return "Workspace unchanged for this orchestration test."
    if name == "Reviewer": return "Review based on supplied deterministic evidence."
    if name == "Tester": return "Inspect the earliest failing check."
    raise AssertionError(f"Unexpected role: {name}")


def test_engine_writes_reports(tmp_path: Path, monkeypatch) -> None:
    # Avoid requiring the external Agents SDK for this orchestration/report test.
    import agentforge.engine as engine_module
    monkeypatch.setattr(engine_module, "build_workspace_tools", lambda workspace: [])
    from agentforge.models import ValidationSummary
    monkeypatch.setattr(engine_module, "validate_workspace", lambda *a, **k: ValidationSummary(passed=False, status="syntax_only", issues=["No tests"]))
    settings = Settings("test-model", tmp_path / "data", 1, 10, 1000, "unused")
    report = run_immediate(AgentForgeEngine(settings, agent_call=fake_agent).run("Test orchestration"))
    assert report.status == "unverified"
    assert Path(report.artifacts["json"]).is_file()
    assert Path(report.artifacts["markdown"]).is_file()
    assert json.loads(Path(report.artifacts["json"]).read_text())["artifacts"] == report.artifacts


def run_immediate(coroutine):
    """Drive fake agents without a socket-backed event loop in restricted Windows CI."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("Fake agent unexpectedly yielded to the event loop")


def test_failed_validation_repair_and_evidence(tmp_path: Path, monkeypatch) -> None:
    import agentforge.engine as engine_module
    monkeypatch.setattr(engine_module, "build_workspace_tools", lambda workspace: [workspace])
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "tests" / "test_value.py").write_text(
        "from sample import value\ndef test_value(): assert value() == 2\n")
    (source / "sample.py").write_text("def value(): return 0\n")
    calls = []

    async def repairing_agent(name, instructions, prompt, *, tools=None, model="unused"):
        calls.append(name)
        if name == "Developer":
            tools[0].write_text("sample.py", "def value(): return 1\n" if calls.count(name) == 1 else "def value(): return 2\n")
        return f"{name} completed"

    settings = Settings("test-model", tmp_path / "data", 1, 15, 1000, "unused", validator="local")
    report = run_immediate(AgentForgeEngine(settings, agent_call=repairing_agent).run(
        "Fix value", source_repo=str(source), allow_local_execution=True))
    assert report.status == "passed", report.error or report.validation.issues
    assert report.repair_rounds == 1
    assert [a["validation"]["status"] for a in report.attempts] == ["failed", "verified"]
    assert "Tester" in calls
    assert "return 1" in report.attempts[0]["diffs"]["sample.py"]
    assert "return 2" in report.attempts[1]["diffs"]["sample.py"]
    assert "return 0" in (source / "sample.py").read_text()
    assert "pytest" in Path(report.artifacts["markdown"]).read_text()
