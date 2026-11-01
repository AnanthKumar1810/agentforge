from pathlib import Path
import pytest
from agentforge.workspace import Workspace, WorkspaceError


def test_write_read_list(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=1000)
    ws.write_text("src/example.py", "print('hello')\n")
    assert ws.read_text("src/example.py") == "print('hello')\n"
    assert ws.list_files() == ["src/example.py"]

@pytest.mark.parametrize("path", ["../outside.txt", "src/../../outside.txt", "/tmp/outside.txt", "C:\\temp\\x.txt"])
def test_reject_path_escape(tmp_path: Path, path: str) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=1000)
    with pytest.raises(WorkspaceError):
        ws.write_text(path, "no")

def test_size_limit(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=3)
    with pytest.raises(WorkspaceError):
        ws.write_text("large.txt", "1234")

@pytest.mark.parametrize("path", ["tests/test_app.py", "test_app.py", "src/widget_test.py", ".env", ".aws/credentials", "server.pem", ".github/workflows/ci.yml", "conftest.py", "pyproject.toml", "sitecustomize.py", "requirements.txt"])
def test_agent_cannot_change_maintained_or_sensitive_files(tmp_path: Path, path: str) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=1000)
    with pytest.raises(WorkspaceError, match="Protected"):
        ws.write_text(path, "modified")

def test_agent_cannot_read_credentials(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=1000)
    (ws.root / ".env").write_text("SECRET=example")
    with pytest.raises(WorkspaceError, match="Credential"):
        ws.read_text(".env")


def test_bounded_read_search_and_exact_replacement(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "workspace", max_file_bytes=1000)
    ws.write_text("src/module.py", "def value():\n    return 0\n")
    assert "src/module.py:2:     return 0" in ws.search_files("return 0")
    assert "src/module.py:1: def value():" in ws.search_files("value()", "src/module.py")
    assert ws.read_lines("src/module.py", 2, 2) == "    return 0\n"
    ws.replace_text("src/module.py", "return 0", "return 2")
    assert ws.read_text("src/module.py").endswith("return 2\n")
    with pytest.raises(WorkspaceError, match="exactly once"):
        ws.replace_text("src/module.py", "return 0", "return 3")
    with pytest.raises(WorkspaceError, match="Line range"):
        ws.read_lines("src/module.py", 1, 201)
    (ws.root / ".env").write_text("SECRET=example")
    assert "SECRET" not in ws.search_files("SECRET")


def test_replace_line_preserves_indent_and_protects_tests(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "workspace")
    ws.write_text("sample.py", "def result():\n    return 0\n")
    ws.replace_line("sample.py", 2, "return 2")
    assert ws.read_text("sample.py") == "def result():\n    return 2\n"
    with pytest.raises(WorkspaceError, match="outside"):
        ws.replace_line("sample.py", 0, "return 3")
    with pytest.raises(WorkspaceError, match="one nonempty"):
        ws.replace_line("sample.py", 2, "return 3\nreturn 4")
    (ws.root / "tests").mkdir()
    (ws.root / "tests" / "test_sample.py").write_text("assert True\n")
    with pytest.raises(WorkspaceError, match="Protected"):
        ws.replace_line("tests/test_sample.py", 1, "assert False")

