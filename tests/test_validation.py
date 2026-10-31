from pathlib import Path
from agentforge.validation import validate_workspace


def test_compile_failure_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "workspace"; root.mkdir()
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = validate_workspace(root, timeout_seconds=10)
    assert not result.passed
    assert any(check.name == "python-syntax" and not check.passed for check in result.checks)


def test_valid_python_compiles(tmp_path: Path) -> None:
    root = tmp_path / "workspace"; root.mkdir()
    (root / "example.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    result = validate_workspace(root, timeout_seconds=10)
    check = next(check for check in result.checks if check.name == "python-syntax")
    assert check.passed
    assert result.status == "syntax_only"
