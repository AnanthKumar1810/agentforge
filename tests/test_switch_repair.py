from pathlib import Path
from types import SimpleNamespace

from agentforge.switch_repair import RepairWorkflow, SwitchConfig
from agentforge.models import ValidationSummary, ValidationCheck


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Offline test unexpectedly awaited I/O")


class FakeEngine:
    def __init__(self, staged):
        self.staged = staged
        self.settings = SimpleNamespace(validation_timeout_seconds=10, validator="local")

    async def run(self, request, **kwargs):
        assert kwargs["allow_local_execution"]
        assert kwargs["source_repo"]
        return SimpleNamespace(status="passed", run_id="demo", workspace=str(self.staged),
            validation=SimpleNamespace(model_dump=lambda **_: {"checks": [{"name": "pytest", "passed": True}],
                "status": "verified"}, checks=[SimpleNamespace(name="pytest", passed=True)]))


def setup(tmp_path):
    source = tmp_path / "source"
    stage = tmp_path / "stage"
    source.mkdir(); stage.mkdir()
    (source / "sample.py").write_text("def value(): return 0\n")
    (stage / "sample.py").write_text("def value(): return 2\n")
    (source / "test_sample.py").write_text("from sample import value\ndef test_value(): assert value() == 2\n")
    (stage / "test_sample.py").write_bytes((source / "test_sample.py").read_bytes())
    messages = []
    async def post(room, text):
        messages.append((room, text))
    config = SwitchConfig("http://localhost:8000", "agent", "secret", "room", "reviewer", source, tmp_path / "state")
    return RepairWorkflow(config, engine=FakeEngine(stage), post=post), source, messages


def test_proposal_then_authorized_approval(tmp_path, monkeypatch):
    monkeypatch.setattr("agentforge.switch_repair.validate_workspace", lambda *a, **k:
        ValidationSummary(passed=True, status="verified", checks=[ValidationCheck(name="pytest", passed=True, exit_code=0, output="1 passed")]))
    flow, source, messages = setup(tmp_path)
    record = run_immediate(flow.propose("fix failing test", "reporter"))
    assert (source / "sample.py").read_text() == "def value(): return 0\n"
    assert "Diff SHA-256" in messages[-1][1]
    assert run_immediate(flow.decide(record["id"], "approve", "stranger"))["status"] == "unauthorized"
    assert (source / "sample.py").read_text() == "def value(): return 0\n"
    approved = run_immediate(flow.decide(record["id"], "approve", "reviewer"))
    assert approved["status"] == "applied"
    assert (source / "sample.py").read_text() == "def value(): return 2\n"
    assert "test logs" in messages[-1][1].lower()
    assert run_immediate(flow.decide(record["id"], "approve", "reviewer"))["status"] == "applied"


def test_reject_and_stale_source(tmp_path):
    flow, source, _ = setup(tmp_path)
    rejected = run_immediate(flow.propose("fix", "reporter"))
    assert run_immediate(flow.decide(rejected["id"], "reject", "reviewer"))["status"] == "rejected"
    assert (source / "sample.py").read_text() == "def value(): return 0\n"
    proposed = run_immediate(flow.propose("fix", "reporter"))
    (source / "sample.py").write_text("other edit\n")
    assert run_immediate(flow.decide(proposed["id"], "approve", "reviewer"))["status"] == "stale"
    assert (source / "sample.py").read_text() == "other edit\n"


def test_unrelated_source_drift_blocks_approval(tmp_path):
    flow, source, _ = setup(tmp_path)
    proposed = run_immediate(flow.propose("fix", "reporter"))
    (source / "test_sample.py").write_text("def test_changed(): assert False\n")
    assert run_immediate(flow.decide(proposed["id"], "approve", "reviewer"))["status"] == "stale"
    assert (source / "sample.py").read_text() == "def value(): return 0\n"


def test_binary_patch_cannot_be_reviewed_in_chat(tmp_path):
    import pytest

    flow, _, _ = setup(tmp_path)
    (flow.engine.staged / "sample.py").write_bytes(b"def value(): return 2\x00\n")
    with pytest.raises(ValueError, match="Binary patch"):
        run_immediate(flow.propose("fix", "reporter"))


def test_only_addressed_room_message_is_handled(tmp_path):
    flow, source, _ = setup(tmp_path)
    frame = {"event": "message", "data": {"room_id": "elsewhere", "payload": {
        "addressed": True, "sender": "reviewer", "body": "repair: fix"}}}
    run_immediate(flow.handle(frame))
    frame["data"]["room_id"] = "room"
    frame["data"]["payload"]["addressed"] = False
    run_immediate(flow.handle(frame))
    assert list((tmp_path / "state").glob("*.json")) == []
    assert (source / "sample.py").read_text() == "def value(): return 0\n"
