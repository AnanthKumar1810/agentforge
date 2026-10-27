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
