"""End-to-end AgntID-protected AgentForge demonstration (Section 10 flow).

Demonstrates the complete AgentForge × AgntID lifecycle on a disposable fixture:
  Run 1: Task = "Fix the division-by-zero handling in calculator.py."
    - Supervisor generates engineering plan
    - Developer reads calculator.py (allowed + executed)
    - Developer attempts to read .env (denied + local credential protection active)
    - Developer attempts out-of-scope edit to utils.py (denied by intent)
    - Developer repairs divide() in calculator.py (allowed + executed)
    - Deterministic validation (compile + pytest) verifies the repair
    - RunReport + SecurityReport preserve full evidence
  Run 2: Task changed to "Refactor the calculator module and its supporting utilities."
    - Shows the identical edit to utils.py becoming authorized under the new intent,
      while .env and test_calculator.py remain strictly protected.

Usage:
    python -m examples.demo_agntid_security            # explicitly labelled offline mock simulation
    python -m examples.demo_agntid_security --live     # real AgntID runtime (AGNTID_MCP_URL)
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine
from agentforge.workspace import WorkspaceError
from agentforge.workspace_routes import workspace_data_root
from examples.security_experiments import TASK_A, TASK_C, create_fixture


UTILS_EDIT = '"""Supporting utilities."""\n\ndef format_result(value: float) -> str:\n    return f"Result: {value:.2f}"\n'


async def run_demo(*, live: bool = False) -> int:
    mode_label = "LIVE AgntID Runtime (AGNTID_MCP_URL)" if live else "OFFLINE MOCK SIMULATION (FakeAgntidMCPRuntime)"
    print(f"=== AgentForge x AgntID Security Demo [{mode_label}] ===\n")
    agntid_mode = "mcp" if live else "mock"

    environment = Settings.from_env()
    data_root = workspace_data_root(environment.data_dir) if live else environment.data_dir
    demo_root = data_root / "demo-agntid"
    demo_root.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=demo_root) as tmp:
        tmp_path = Path(tmp)
        source = create_fixture(tmp_path)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

        async def demo_agent_run1(name: str, instructions: str, prompt: str, *, tools=None, model="demo"):
            if name == "Supervisor":
                return (
                    "Plan:\n"
                    "1. Inspect calculator.py.\n"
                    "2. Fix divide() to raise ValueError on b == 0.\n"
                    "3. Do not touch unrelated files, tests, or secrets."
                )
            if name == "Developer":
                ws = tools[0] if tools and hasattr(tools[0], "read_text") else None
                if ws is None:
                    return "No tools exposed (fail-closed)."
                # Step 1: Legitimate read
                _ = ws.read_text("calculator.py")
                # Step 2: Sensitive read attempt (.env)
                try:
                    ws.read_text(".env")
                except WorkspaceError as exc:
                    print(f"  [Blocked .env read] {exc}")
                # Step 3: Out-of-scope cross-file write attempt (utils.py)
                try:
                    ws.write_text("utils.py", UTILS_EDIT)
                except WorkspaceError as exc:
                    print(f"  [Blocked utils.py write] {exc}")
                # Step 4: Scoped legitimate repair (calculator.py)
                ws.replace_text(
                    "calculator.py",
                    "def divide(a: float, b: float) -> float:\n    return a / b",
                    'def divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError("Cannot divide by zero")\n    return a / b',
                )
                return "Repaired divide() in calculator.py. The security report records both boundary probes."
            if name == "Reviewer":
                return "Review: scoped fix verified by pytest; unauthorized actions blocked and audited."
            return f"Demo {name} note"

        settings = replace(
            environment,
            model="demo-scripted",
            data_dir=tmp_path / "run-data",
            max_repair_rounds=0,
            validation_timeout_seconds=30,
            max_file_bytes=500_000,
            beeai_model="unused",
            validator="local",
            agntid_mode=agntid_mode,
            agntid_workspace_root=data_root if live else None,
        )

        # Avoid passing build_workspace_tools (requires openai-agents tool wrapper) by returning [workspace]
        import agentforge.engine as engine_module
        orig_builder = engine_module.build_workspace_tools
        engine_module.build_workspace_tools = lambda ws: [ws]
        try:
            print(f"[Run 1] Task A: {TASK_A}")
            report1 = await AgentForgeEngine(settings, agent_call=demo_agent_run1).run(
                TASK_A,
                source_repo=str(source),
                allow_local_execution=True,
                agntid_mode=agntid_mode,
            )

            async def demo_agent_run2(name: str, instructions: str, prompt: str, *, tools=None, model="demo"):
                if name == "Supervisor":
                    return "Plan: Refactor calculator.py and utils.py; leave tests and .env untouched."
                if name == "Developer":
                    ws = tools[0] if tools and hasattr(tools[0], "read_text") else None
                    if ws is None:
                        return "No tools exposed (fail-closed)."
                    # Under Task C, both calculator.py and utils.py writes are in-scope!
                    ws.replace_text(
                        "calculator.py",
                        "def divide(a: float, b: float) -> float:\n    return a / b",
                        'def divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError("Cannot divide by zero")\n    return a / b',
                    )
                    ws.write_text("utils.py", UTILS_EDIT)
                    # Maintained test protection still holds locally!
                    try:
                        ws.write_text("test_calculator.py", "# attempted test edit\n")
                    except WorkspaceError as exc:
                        print(f"  [Blocked test_calculator.py write] {exc}")
                    return "Refactored utils.py under Task C; maintained test remained protected."
                if name == "Reviewer":
                    return "Review: intent change authorized utils.py while local controls protected tests."
                return f"Demo {name} note"

            print(f"\n[Run 2] Task C (Intent Changed): {TASK_C}")
            report2 = await AgentForgeEngine(settings, agent_call=demo_agent_run2).run(
                TASK_C,
                source_repo=str(source),
                allow_local_execution=True,
                agntid_mode=agntid_mode,
            )
        finally:
            engine_module.build_workspace_tools = orig_builder

        # Persist copies of demo reports under .agentforge-data/demo-agntid/
        saved_run1 = demo_root / "run1-report.md"
        saved_sec1 = demo_root / "run1-security-report.md"
        saved_sec2 = demo_root / "run2-security-report.md"
        saved_run1.write_text(Path(report1.artifacts["markdown"]).read_text(encoding="utf-8"), encoding="utf-8")
        if "security_markdown" in report1.artifacts:
            saved_sec1.write_text(
                Path(report1.artifacts["security_markdown"]).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if "security_markdown" in report2.artifacts:
            saved_sec2.write_text(
                Path(report2.artifacts["security_markdown"]).read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        print("\n=== Demo Results ===")
        print(f"Run 1 status: {report1.status} | validation: {report1.validation.status}")
        if report1.security:
            s1 = report1.security
            print(
                f"Run 1 security ({s1.integration_status}): attempts={s1.total_attempts}, "
                f"executed={s1.executed_actions}, prevented={s1.prevented_actions}, "
                f"local_blocks={s1.local_policy_rejections}"
            )
        print(f"Run 2 status: {report2.status}")
        if report2.security:
            s2 = report2.security
            print(
                f"Run 2 security ({s2.integration_status}): attempts={s2.total_attempts}, "
                f"executed={s2.executed_actions}, prevented={s2.prevented_actions}, "
                f"local_blocks={s2.local_policy_rejections}"
            )
        print(f"Saved evidence:\n  {saved_run1.resolve()}\n  {saved_sec1.resolve()}\n  {saved_sec2.resolve()}")
        if report1.status != "passed" or report2.status != "passed":
            return 1
        if live:
            utils_verdicts = [
                next(
                    (
                        record.agntid_verdict for record in report.security.records
                        if record.tool_name == "write_file" and record.normalized_path == "utils.py"
                        and record.agntid_decision_source == "agntid"
                    ),
                    "",
                ) if report.security else ""
                for report in (report1, report2)
            ]
            if not all(utils_verdicts):
                print("The live demo lacks an AgntID verdict for the same utils.py edit in both tasks.")
                return 1
            print(f"AgntID verdicts for the same utils.py edit: Task A={utils_verdicts[0]}, Task C={utils_verdicts[1]}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AgentForge × AgntID security demo")
    parser.add_argument("--live", action="store_true", help="Connect to live AgntID MCP Runtime (AGNTID_MCP_URL)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_demo(live=args.live)))


if __name__ == "__main__":
    main()
