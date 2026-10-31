"""Deterministic build, validate, bounded repair, review, and report lifecycle."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from pathlib import Path
import uuid

from agentforge.agents import build_workspace_tools, run_agent
from agentforge.config import Settings
from agentforge.integrations import run_beeai_risk_review, run_crewai_planning
from agentforge.models import RunReport, ValidationSummary
from agentforge.reporting import write_reports
from agentforge.validation import validate_workspace
from agentforge.workspace import Workspace, PROTECTED_FILES
from agentforge.source import import_source, snapshot_bytes, changed_files
from agentforge.test_policy import select_tests
import difflib

AgentCall = Callable[..., Awaitable[str]]

SUPERVISOR = "Convert the user request into a concise engineering plan, assumptions, acceptance criteria, likely files, and tests. Do not claim to edit files."
DEVELOPER = (
    "Implement the task only with read_file, read_lines, search_files, write_file, replace_text, and list_files. Use relative paths; "
    "never attempt shell, network, credential, or outside-workspace access. Write complete, "
    "testable files and summarize changes and remaining risks."
)
TESTER = "Interpret the deterministic validation evidence, rank likely causes, and suggest the smallest fixes. Do not claim to run tests yourself."
REVIEWER = ("Review request, plan, change summary, and validation evidence. "
            "The deterministic validation status is authoritative. Never claim tests passed if status is failed or unverified. "
            "Separate verified facts from assumptions; flag correctness, security, and missing-test risks.")


class AgentForgeEngine:
    def __init__(self, settings: Settings | None = None, *, agent_call: AgentCall | None = None):
        self.settings = settings or Settings.from_env()
        self.agent_call = agent_call or run_agent

    async def _ask(self, name: str, instructions: str, prompt: str, tools: list[Any] | None = None) -> str:
        if self.agent_call is run_agent:
            return await self.agent_call(name, instructions, prompt, tools=tools or [], model=self.settings.model,
                                         provider=self.settings.provider, ollama_base_url=self.settings.ollama_base_url)
        return await self.agent_call(name, instructions, prompt, tools=tools or [], model=self.settings.model)

    async def run(
        self, request: str, *, use_crewai: bool = False, use_beeai: bool = False,
        max_repair_rounds: int | None = None, source_repo: str | None = None,
        test_manifest: str | None = None, generate_tests: bool = False,
        allow_local_execution: bool = False,
    ) -> RunReport:
        if not request or not request.strip():
            raise ValueError("request cannot be empty")
        if len(request) > 20_000:
            raise ValueError("request is too long (maximum 20,000 characters)")
        repair_limit = self.settings.max_repair_rounds if max_repair_rounds is None else max_repair_rounds
        if not isinstance(repair_limit, int) or not 0 <= repair_limit <= 5:
            raise ValueError("max_repair_rounds must be an integer from 0 to 5")

        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc)
        run_dir = (self.settings.data_dir / "runs" / run_id).resolve()
        workspace_path = run_dir / "workspace"
        workspace = Workspace(workspace_path, max_file_bytes=self.settings.max_file_bytes)
        output_dir = run_dir / "artifacts"
        plan, review, changes_summary, error = "", "", "", None
        optional_context: dict[str, str] = {}
        attempts: list[dict[str, Any]] = []
        baseline: dict[str, bytes] = {}
        tester_notes: list[str] = []
        repair_rounds = 0
        validation = ValidationSummary(passed=False, issues=["Validation has not run"])

        try:
            if source_repo:
                optional_context.update(import_source(source_repo, workspace_path))
            baseline = snapshot_bytes(workspace_path)
            plan = await self._ask("Supervisor", SUPERVISOR, f"User request:\n{request}")
            if use_crewai:
                optional_context["crewai_plan"] = await run_crewai_planning(request, self.settings.model)
            if use_beeai:
                optional_context["beeai_risk_review"] = await run_beeai_risk_review(request, plan, self.settings.beeai_model)
            tools = [workspace] if self.settings.provider == "ollama" and self.agent_call is run_agent else build_workspace_tools(workspace)
            changes_summary = await self._ask(
                "Developer", DEVELOPER,
                f"USER REQUEST\n{request}\n\nPLAN\n{plan}\n\nOPTIONAL CONTEXT\n{optional_context or '(none)'}\n\nImplement the task. Inspect existing files before replacing them; add tests where practical.",
                tools=tools,
            )
            for attempt in range(repair_limit + 1):
                current = snapshot_bytes(workspace_path)
                changed = changed_files(baseline, current)
                protected = [path for path in changed if path in baseline and (
                    path.startswith("tests/") or path.startswith(".github/")
                    or Path(path).name.lower() in PROTECTED_FILES
                    or Path(path).name.lower().startswith("requirements") and path.endswith(".txt")
                    or path.startswith(".env") or path.endswith("_test.py")
                    or Path(path).name.startswith("test_") and path.endswith(".py")
                )]
                if protected:
                    validation = ValidationSummary(passed=False, status="failed", issues=[
                        "Maintained or protected files changed: " + ", ".join(protected)])
                elif not allow_local_execution:
                    validation = ValidationSummary(passed=False, status="syntax_only", issues=[
                        "Code execution disabled. Review the workspace, then rerun with --allow-local-execution only in a trusted or isolated environment."])
                else:
                    selected = select_tests(workspace_path, changed, test_manifest)
                    validation = validate_workspace(workspace_path, timeout_seconds=self.settings.validation_timeout_seconds, selected_tests=selected, runner=self.settings.validator)
                diffs = {}
                for path in changed:
                    old = baseline.get(path, b"").decode("utf-8", "replace").splitlines(keepends=True)
                    new = current.get(path, b"").decode("utf-8", "replace").splitlines(keepends=True)
                    diffs[path] = "".join(difflib.unified_diff(old, new, fromfile="baseline/" + path, tofile="workspace/" + path))[:20000]
                attempts.append({"round": attempt, "changed_files": changed, "diffs": diffs, "validation": validation.model_dump(mode="json")})
                if validation.status == "syntax_only" or validation.passed or protected or attempt >= repair_limit:
                    break
                repair_rounds += 1
                tester_note = await self._ask(
                    "Tester", TESTER,
                    f"Task: {request}\n\nValidation evidence:\n{validation.model_dump_json(indent=2)}",
                )
                tester_notes.append(tester_note)
                changes_summary = await self._ask(
                    "Developer", DEVELOPER,
                    f"Original task:\n{request}\n\nPrevious change summary:\n{changes_summary}\n\n"
                    f"Validation evidence:\n{validation.model_dump_json(indent=2)}\n\nTester diagnosis:\n{tester_note}\n\n"
                    "Make the smallest justified repair with workspace tools. Do not weaken or delete tests to make validation green.",
                    tools=tools,
                )
            if generate_tests and validation.status != "verified":
                optional_context["proposed_tests"] = await self._ask(
                    "TestDesigner", "Propose tests for uncovered acceptance criteria. Do not edit files or claim they ran.",
                    f"Request: {request}\nValidation: {validation.model_dump_json()}\nChanged: {changed}",
                )
            review = await self._ask(
                "Reviewer", REVIEWER,
                f"Request:\n{request}\n\nPlan:\n{plan}\n\nChange summary:\n{changes_summary}\n\n"
                f"Validation evidence:\n{validation.model_dump_json(indent=2)}\n\nFiles and sizes:\n{workspace.snapshot()!r}",
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        status = "error" if error else ("passed" if validation.status == "verified" else "unverified" if validation.status == "syntax_only" else "failed")
        report = RunReport(
            run_id=run_id, request=request, status=status, started_at=started,
            finished_at=datetime.now(timezone.utc), workspace=str(workspace_path.resolve()),
            plan=plan, optional_context=optional_context, validation=validation,
            repair_rounds=repair_rounds, attempts=attempts, tester_notes=tester_notes, review=review, error=error,
            metadata={"model": self.settings.model, "provider": self.settings.provider, "validator": self.settings.validator, "max_repair_rounds": repair_limit, "change_summary": changes_summary, "allow_local_execution": allow_local_execution},
        )
        report.artifacts = write_reports(report, output_dir)
        return report
