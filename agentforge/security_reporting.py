"""Auditable security report generation for AgntID-protected AgentForge runs and experiments.

Separates:
- Real AgntID decisions (``agntid``)
- Explicitly labelled mock simulation decisions (``mock``)
- AgentForge local workspace policy rejections (``local_policy``)
- Fail-closed authorization errors (``fail_closed``)
- Tool execution outcomes vs model-generated explanations
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge.security_models import (
    ExperimentSuite,
    SecuritySummary,
)


def write_security_report(
    summary: SecuritySummary,
    output_dir: Path,
    *,
    experiment_suite: ExperimentSuite | None = None,
) -> dict[str, str]:
    """Write ``security_report.json`` and ``security_report.md`` into ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "security_report.json"
    md_path = output_dir / "security_report.md"

    payload = summary.model_dump(mode="json")
    if experiment_suite is not None:
        payload["experiment_suite"] = experiment_suite.model_dump(mode="json")

    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        "\n".join(build_security_markdown_lines(summary, experiment_suite=experiment_suite)),
        encoding="utf-8",
    )
    return {
        "security_json": str(json_path.resolve()),
        "security_markdown": str(md_path.resolve()),
    }


def build_security_markdown_lines(
    summary: SecuritySummary,
    *,
    experiment_suite: ExperimentSuite | None = None,
    heading_level: int = 1,
) -> list[str]:
    """Render structured security evidence lines suitable for standalone or embedded reports."""
    h1 = "#" * heading_level
    h2 = "#" * (heading_level + 1)
    h3 = "#" * (heading_level + 2)

    lines: list[str] = [
        f"{h1} AgentForge Security & AgntID Authorization Report",
        "",
        f"- Integration status: `{summary.integration_status}`",
        f"- Intended task: {summary.intended_task or '(not set)'}",
        f"- AgntID task ID: `{summary.task_id or '(none)'}`",
        f"- Agent ID: `{summary.agent_id or '(unset)'}` | User ID: `{summary.user_id or '(unset)'}`",
        f"- Intent engine: `{summary.intent_engine_id or '(none)'}` | Snapshot ID: `{summary.snapshot_id or '(none)'}`",
        f"- Eligible tools: {', '.join(summary.eligible_tools) or '(none)'}",
        f"- Prompt-shortlisted tools: {', '.join(summary.shortlisted_tools) or '(none)'}",
    ]
    if summary.extracted_constraints:
        lines.append(f"- Extracted constraints: {'; '.join(summary.extracted_constraints)}")
    if summary.evidence_references:
        lines.append(f"- Evidence references: {', '.join(summary.evidence_references)}")

    if summary.integration_status == "mock_simulation":
        lines += [
            "",
            "> **OFFLINE SIMULATION NOTICE:** This run used `FakeAgntidMCPRuntime` (mock mode). "
            "Decisions marked `mock` demonstrate the AgntID SDK wire contract locally and are NOT "
            "live AgntID control-plane decisions.",
        ]

    lines += [
        "",
        f"{h2} Authorization & Enforcement Counts",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total authorization attempts | {summary.total_attempts} |",
        f"| Real AgntID allows (`agntid`) | {summary.agntid_allows} |",
        f"| Real AgntID denies (`agntid`) | {summary.agntid_denies} |",
        f"| Real AgntID restrictions / reviews (`agntid`) | {summary.agntid_restrictions} |",
        f"| Mock simulation allows (`mock`) | {summary.mock_allows} |",
        f"| Mock simulation denies (`mock`) | {summary.mock_denies} |",
        f"| Mock simulation restrictions / reviews (`mock`) | {summary.mock_restrictions} |",
        f"| AgentForge local workspace policy rejections (`local_policy`) | {summary.local_policy_rejections} |",
        f"| Actions actually executed | {summary.executed_actions} |",
        f"| Actions prevented | {summary.prevented_actions} |",
        f"| Authorization / integration errors (fail-closed) | {summary.integration_errors} |",
        "",
    ]

    if summary.records:
        lines += [
            f"{h2} Authorization & Execution Trace",
            "",
            "| # | Timestamp | Tool | Normalized Path | Decision | Auth Source | Runtime Verdict | Local Policy | Executed | Policy / Correlation | Explanation |",
            "|---|-----------|------|-----------------|----------|-------------|-----------------|--------------|----------|----------------------|-------------|",
        ]
        for idx, rec in enumerate(summary.records, 1):
            ts = rec.timestamp.strftime("%H:%M:%SZ")
            path_disp = (rec.normalized_path or rec.target_resource or ".")[:36]
            runtime_verdict = (
                f"{rec.agntid_decision_source}:{rec.agntid_verdict}"
                if rec.agntid_verdict
                else "-"
            )
            local_flag = "REJECTED" if rec.local_policy_rejected else "ok"
            corr = rec.policy_name or rec.correlation_id or "-"
            expl = (rec.explanation or rec.error or "").replace("|", "/").replace("\n", " ")[:90]
            lines.append(
                f"| {idx} | {ts} | `{rec.tool_name}` | `{path_disp}` | "
                f"`{rec.decision.value}` | `{rec.decision_source.value}` | `{runtime_verdict}` | "
                f"{local_flag} | {'Yes' if rec.executed else 'No'} | `{corr[:30]}` | {expl} |"
            )
        lines.append("")

    if experiment_suite is not None and experiment_suite.experiments:
        lines += [
            f"{h2} Security Experiment Findings (Suite `{experiment_suite.suite_id}`)",
            "",
        ]
        for exp in experiment_suite.experiments:
            dec = exp.actual_decision.value if exp.actual_decision else "N/A"
            src = exp.actual_decision_source.value if exp.actual_decision_source else "N/A"
            lines += [
                f"{h3} {exp.experiment_id}: {exp.name}",
                "",
                f"- **Category:** {exp.category or 'General'}",
                f"- **Intended task:** {exp.intended_task}",
                f"- **Attempted action:** `{exp.tool_name}` (`{exp.action_type.value}`) on `{exp.target_resource}`",
                f"- **Expected outcome:** {exp.expected_outcome}",
                f"- **Actual authoritative decision:** `{dec}` (source: `{src}`)",
                f"- **Runtime verdict:** `{exp.agntid_verdict or 'n/a'}` | **Local policy blocked:** {'Yes' if exp.local_policy_rejected else 'No'}",
                f"- **Executed:** {'Yes' if exp.executed else 'No'}",
                f"- **Explanation:** {exp.actual_explanation or '(none)'}",
                f"- **Security interpretation:** {exp.security_interpretation}",
                "",
            ]

    lines += [
        f"{h2} Decision Source Legend",
        "",
        "| Source | Authoritative Meaning |",
        "|--------|-----------------------|",
        "| `agntid` | Evaluated and decided by a live AgntID MCP Runtime (`AGNTID_MCP_URL`) |",
        "| `mock` | Evaluated by the offline `FakeAgntidMCPRuntime` simulation (NOT live AgntID) |",
        "| `local_policy` | Blocked independently by AgentForge's deterministic `Workspace` controls |",
        "| `local_gate` | Blocked locally by the task shortlist or retry limit before an action request |",
        "| `fail_closed` | Authorization could not be verified (timeout, missing intent, malformed/unavailable runtime); execution blocked |",
        "",
    ]
    return lines
