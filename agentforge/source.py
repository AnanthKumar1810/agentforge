"""Copy a local source tree to a per-run workspace; never modify the source."""
import shutil
import fnmatch
import os
import subprocess
from pathlib import Path

EXCLUDED = {".git", ".venv", "venv", "__pycache__", ".agentforge-data", ".aws", ".ssh",
            ".env*", "*.pem", "*.key", ".npmrc", ".pypirc", "id_rsa*", "id_ed25519*", "credentials.json"}
MAX_FILES = 3000
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 5 * 1024 * 1024


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in EXCLUDED)


def _check_source(root: Path) -> None:
    count = total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError("source repository contains symbolic links")
        dirs[:] = [name for name in dirs if not _excluded(name)]
        for name in files:
            if _excluded(name):
                continue
            count += 1
            size = (Path(directory) / name).stat().st_size
            total += size
            if count > MAX_FILES or total > MAX_SOURCE_BYTES or size > MAX_SOURCE_FILE_BYTES:
                raise ValueError("source repository exceeds import limits (3000 files, 50 MiB total, 5 MiB per file)")

def import_source(source: str, workspace: Path) -> dict[str, str]:
    root = Path(source).expanduser().resolve(strict=True)
    if not root.is_dir() or root == workspace or root in workspace.parents:
        raise ValueError("source_repo must be an existing, separate directory")
    _check_source(root)
    shutil.copytree(root, workspace, dirs_exist_ok=True,
                    ignore=lambda _directory, names: [name for name in names if _excluded(name)])
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        commit = proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        commit = "unknown"
    return {"source": str(root), "commit": commit}

def snapshot_bytes(workspace: Path) -> dict[str, bytes]:
    return {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*") if p.is_file() and not p.is_symlink() and ".pytest_cache" not in p.parts and "__pycache__" not in p.parts}

def changed_files(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
