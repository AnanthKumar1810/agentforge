"""Exercise the validator container against the hackathon repair fixture."""

import shutil
from pathlib import Path

from agentforge.validation import validate_workspace


def test_docker_validator_detects_and_verifies_fixture(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples" / "repair_target"
    target = tmp_path / "repair_target"
    shutil.copytree(source, target)

    failing = validate_workspace(target, runner="docker")
    assert not failing.passed
    assert any(check.name == "pytest" and check.exit_code == 1 for check in failing.checks)

    (target / "sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    passing = validate_workspace(target, runner="docker")
    assert passing.passed, passing.issues
