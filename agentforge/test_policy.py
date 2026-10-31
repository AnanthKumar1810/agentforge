"""Maintained test selection. The manifest is trusted configuration, never agent output."""
import fnmatch
import json
from pathlib import Path

def select_tests(workspace: Path, changed: list[str], manifest_path: str | None) -> list[str]:
    if not manifest_path:
        return []
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("suites"), list):
        raise ValueError("test manifest requires a suites list")
    selected = set()
    for suite in manifest["suites"]:
        patterns = suite["when"]
        targets = suite["tests"]
        if not isinstance(patterns, list) or not isinstance(targets, list):
            raise ValueError("suite when and tests must be lists")
        if any(fnmatch.fnmatch(path, pattern) for path in changed for pattern in patterns):
            for target in targets:
                if not isinstance(target, str) or not target.startswith("tests/"):
                    raise ValueError("test targets must be relative paths under tests/")
                resolved = (workspace / target).resolve()
                if not resolved.is_relative_to(workspace.resolve()) or not resolved.is_file():
                    raise ValueError(f"maintained test missing: {target}")
                selected.add(target)
    return sorted(selected)
