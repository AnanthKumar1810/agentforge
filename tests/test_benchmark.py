"""The guided benchmark must offer only edits its workspace accepts."""

from pathlib import Path

import pytest

from benchmarks.repository_mutations import CASES, SeededRepairWorkspace
from agentforge.workspace import WorkspaceError


def test_seeded_cases_use_the_guarded_edit_tool(tmp_path: Path) -> None:
    for case in CASES:
        assert "replace_line" not in case.request
        target = tmp_path / case.name / case.file
        target.parent.mkdir(parents=True)
        target.write_text(case.mutant + "\n", encoding="utf-8")
        workspace = SeededRepairWorkspace(
            tmp_path / case.name, target=case.file, injected=case.mutant,
            expected=case.original, max_file_bytes=500_000,
        )
        assert workspace.ollama_tool_allowlist == {
            "read_file", "read_lines", "search_files", "replace_text",
        }
        with pytest.raises(WorkspaceError):
            workspace.replace_line(case.file, 1, case.original)
        workspace.replace_text(case.file, case.mutant, case.original)
        assert target.read_text(encoding="utf-8") == case.original + "\n"
