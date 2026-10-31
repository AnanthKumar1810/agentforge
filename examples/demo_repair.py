"""Offline deterministic workflow demo; fake agents stand in for model responses.

Run from the repository root: python -m examples.demo_repair
This exercises the real source import, validator, bounded repair, and reports.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import os

import agentforge.engine as engine_module
from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine


def run_immediate(coroutine):
    # The fake agent awaits only immediate responses. No network/event loop is needed.
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    raise RuntimeError("Demo unexpectedly yielded to an event loop")


def main():
    demo_dir = Path.cwd() / ".agentforge-data"
    demo_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=demo_dir) as directory:
        root = Path(directory)
        source = root / "source"
        (source / "tests").mkdir(parents=True)
        (source / "sample.py").write_text("def value(): return 0\n")
        (source / "tests" / "test_value.py").write_text(
            "from sample import value\ndef test_value(): assert value() == 2\n")
        os.environ["TEMP"] = str(root)
        os.environ["TMP"] = str(root)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        engine_module.build_workspace_tools = lambda workspace: [workspace]
        developer_calls = 0

        async def fake_agent(name, instructions, prompt, *, tools=None, model="demo"):
            nonlocal developer_calls
            if name == "Developer":
                developer_calls += 1
                tools[0].write_text("sample.py", f"def value(): return {developer_calls}\n")
            return f"Offline {name} response"

        settings = Settings("offline-demo", root / "run-data", 1, 15, 1000, "unused", validator="local")
        report = run_immediate(AgentForgeEngine(settings, agent_call=fake_agent).run(
            "Make value() return 2", source_repo=str(source), allow_local_execution=True))
        print(f"Status: {report.status}; attempts: {len(report.attempts)}; repairs: {report.repair_rounds}")
        for attempt in report.attempts:
            print(f"Round {attempt['round']}: {attempt['validation']['status']}")
        print(f"Original source: {(source / 'sample.py').read_text().strip()}")
        print(f"Report: {report.artifacts['markdown']}")
        # Keep evidence after the temporary fixture exits.
        destination = demo_dir / "demo-report.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(Path(report.artifacts["markdown"]).read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Saved evidence: {destination}")
        if report.status != "passed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
