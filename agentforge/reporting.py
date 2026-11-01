"""Human- and machine-readable report output."""

from __future__ import annotations
import json
from pathlib import Path
from agentforge.models import RunReport


def write_reports(report: RunReport, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    report.artifacts = {"json": str(json_path.resolve()), "markdown": str(markdown_path.resolve())}
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# AgentForge run report", "", f"- Run ID: {report.run_id}",
        f"- Status: {report.status}", f"- Started: {report.started_at.isoformat()}",
        f"- Finished: {report.finished_at.isoformat()}", f"- Workspace: {report.workspace}",
        f"- Repair rounds: {report.repair_rounds}", "", "## Request", "", report.request,
        "", "## Plan", "", report.plan or "(No plan output)", "", "## Validation", "",
        f"Overall result: {report.validation.status.upper()} ({'checks passed' if report.validation.passed else 'acceptance not established'})", "",
    ]
    for check in report.validation.checks:
        lines += [f"### {check.name}: {'PASS' if check.passed else 'FAIL'}", "",
                  f"- Duration: {check.duration_seconds:.3f}s", f"- Exit code: {check.exit_code}",
                  "", "Output:", "", check.output or "(no output)", ""]
    if report.validation.issues:
        lines += ["### Issues", ""] + [f"- {item}" for item in report.validation.issues] + [""]
    if report.tester_notes:
        lines += ["## Tester notes", ""] + [f"- {item}" for item in report.tester_notes] + [""]
    lines += ["## Attempts", ""]
    for attempt in report.attempts:
        lines += [f"### Round {attempt['round']}", "", f"Changed: {', '.join(attempt['changed_files']) or 'none'}", "", f"Validation: {attempt['validation']['status']}", ""]
        for check in attempt["validation"].get("checks", []):
            lines += [f"- {check['name']}: {'PASS' if check['passed'] else 'FAIL'} (exit {check['exit_code']})", "", "```text", check['output'], "```", ""]
        for issue in attempt["validation"].get("issues", []):
            lines += [f"- Issue: {issue}"]
        for path, diff in attempt["diffs"].items():
            lines += [f"#### {path}", "", "```diff", diff, "```", ""]
    lines += ["## Model review (interpretation, not validation evidence)", "", report.review or "(No review output)", ""]
    if report.error:
        lines += ["## Error", "", report.error, ""]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return report.artifacts
