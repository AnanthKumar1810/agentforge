"""Path-confined file tools for a single AgentForge run.

This is defense in depth, not an OS sandbox. Run untrusted generated code in an isolated
container or VM before using AgentForge for anything beyond local experimentation.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

from agentforge.config import Settings


class WorkspaceError(ValueError):
    """A file request violates the workspace policy."""


PROTECTED_FILES = {"conftest.py", "pytest.ini", "tox.ini", "test_manifest.json",
                   "pyproject.toml", "setup.cfg", "setup.py", "sitecustomize.py", "usercustomize.py"}

class Workspace:
    def __init__(self, root: Path, *, max_file_bytes: int | None = None) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes if max_file_bytes is not None else Settings.from_env().max_file_bytes

    def resolve(self, relative_path: str) -> Path:
        if not relative_path or not relative_path.strip():
            raise WorkspaceError("Path cannot be empty")
        posix_path = Path(relative_path)
        windows_path = PureWindowsPath(relative_path)
        if posix_path.is_absolute() or posix_path.drive or windows_path.is_absolute() or windows_path.drive:
            raise WorkspaceError("Absolute paths are not allowed")
        normalized = relative_path.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise WorkspaceError("Parent-directory traversal is not allowed")
        target = (self.root / normalized).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("Path resolves outside the run workspace") from exc
        return target

    def read_text(self, relative_path: str) -> str:
        target = self.resolve(relative_path)
        if self._sensitive(target):
            raise WorkspaceError("Credential path cannot be read by the agent")
        if not target.is_file():
            raise WorkspaceError(f"File does not exist: {relative_path}")
        if target.stat().st_size > self.max_file_bytes:
            raise WorkspaceError("File exceeds the configured size limit")
        return target.read_text(encoding="utf-8")

    def read_lines(self, relative_path: str, start_line: int, end_line: int) -> str:
        if not isinstance(start_line, int) or not isinstance(end_line, int) or start_line < 1 or end_line < start_line or end_line - start_line >= 200:
            raise WorkspaceError("Line range must be 1-based and at most 200 lines")
        lines = self.read_text(relative_path).splitlines(keepends=True)
        if start_line > len(lines):
            raise WorkspaceError("Start line exceeds the file length")
        return "".join(lines[start_line - 1:end_line])

    def search_files(self, query: str, relative_path: str = ".") -> str:
        if not query or len(query) > 200:
            raise WorkspaceError("Search query must be 1 to 200 characters")
        base = self.root if relative_path == "." else self.resolve(relative_path)
        paths = [base] if base.is_file() else list(base.rglob("*.py"))
        results = []
        for path in paths[:1000]:
            if not path.is_file() or self._sensitive(path):
                continue
            try:
                for number, line in enumerate(self.read_text(path.relative_to(self.root).as_posix()).splitlines(), 1):
                    if query.lower() in line.lower():
                        results.append(f"{path.relative_to(self.root).as_posix()}:{number}: {line[:200]}")
                        if len(results) == 50:
                            return "\n".join(results) + "\n[search truncated at 50 matches]"
            except (OSError, UnicodeError, WorkspaceError):
                continue
        return "\n".join(results) or "(no matches)"

    def replace_text(self, relative_path: str, old: str, new: str) -> str:
        if not old or len(old.encode("utf-8")) > self.max_file_bytes:
            raise WorkspaceError("Replacement source must be nonempty and within the file size limit")
        content = self.read_text(relative_path)
        if content.count(old) != 1:
            raise WorkspaceError("Replacement source must occur exactly once")
        return self.write_text(relative_path, content.replace(old, new, 1))

    def write_text(self, relative_path: str, content: str) -> str:
        target = self.resolve(relative_path)
        parts = target.relative_to(self.root).parts
        name = target.name.lower()
        if (any(part.lower() in {"tests", ".git", ".github", ".venv"} for part in parts)
                or self._sensitive(target) or name in PROTECTED_FILES
                or name.startswith("requirements") and name.endswith(".txt")
                or name.startswith("test_") and name.endswith(".py")
                or name.endswith("_test.py")):
            raise WorkspaceError("Protected test, configuration, or credential path cannot be edited by the agent")
        payload = content.encode("utf-8")
        if len(payload) > self.max_file_bytes:
            raise WorkspaceError("File exceeds the configured size limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise WorkspaceError("Writing through a symbolic link is not allowed")
        target.write_text(content, encoding="utf-8", newline="\n")
        return f"Wrote {relative_path} ({len(payload)} bytes)"

    def _sensitive(self, target: Path) -> bool:
        parts = target.relative_to(self.root).parts
        name = target.name.lower()
        return (any(part.lower() in {".aws", ".ssh"} for part in parts)
                or name.startswith(".env") or name.endswith((".pem", ".key"))
                or name in {".npmrc", ".pypirc", "credentials.json"}
                or name.startswith(("id_rsa", "id_ed25519")))

    def list_files(self, relative_path: str = ".") -> list[str]:
        base = self.root if relative_path == "." else self.resolve(relative_path)
        if not base.is_dir():
            raise WorkspaceError(f"Directory does not exist: {relative_path}")
        found: list[str] = []
        for item in base.rglob("*"):
            try:
                item.resolve().relative_to(self.root)
            except (ValueError, OSError):
                continue
            if item.is_file():
                found.append(item.relative_to(self.root).as_posix())
            if len(found) >= 1000:
                found.append("[listing truncated at 1000 files]")
                break
        return sorted(found)

    def snapshot(self) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        for relative in self.list_files():
            if relative.startswith("["):
                continue
            try:
                snapshot[relative] = self.resolve(relative).stat().st_size
            except OSError:
                continue
        return snapshot
