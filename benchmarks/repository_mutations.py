"""Pinned public-repository mutation benchmark; run tests only in Docker."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from agentforge.agents import run_agent
from agentforge.config import Settings
from agentforge.engine import DEVELOPER, AgentForgeEngine
from agentforge.source import changed_files, import_source, snapshot_bytes
from agentforge.validation import validate_workspace
from agentforge.workspace import Workspace


@dataclass(frozen=True)
class Case:
    name: str
    url: str
    commit: str
    file: str
    original: str
    mutant: str
    request: str


CASES = (
    Case(
        "boltons-clamp", "https://github.com/mahmoud/boltons.git",
        "d31d821d56c8f7756af9c30a864077f2e5855321", "boltons/mathutils.py",
        "return min(max(x, lower), upper)", "return max(min(x, lower), upper)",
        "Fix the regression in boltons/mathutils.py clamp(): values inside and outside the "
        "lower/upper bounds should behave as the maintained tests specify. Read "
        "lines 40 through 72 of boltons/mathutils.py to inspect the whole function; "
        "then use replace_line for its return statement. Do not edit tests.",
    ),
    Case(
        "more-itertools-take", "https://github.com/more-itertools/more-itertools.git",
        "08dca284ba5446a026afdc34067b658f9da989eb", "more_itertools/recipes.py",
        "return list(islice(iterable, n))", "return list(islice(iterable, n + 1))",
        "Fix the off-by-one regression in more_itertools/recipes.py take(). Its maintained "
        "tests specify the correct number of items. Read lines 113 through 129 of "
        "more_itertools/recipes.py to inspect the whole function; then use "
        "replace_line for its return statement. Do not edit tests.",
    ),
    Case(
        "itsdangerous-compressed-payload", "https://github.com/pallets/itsdangerous.git",
        "672971d66a2ef9f85151e53283113f33d642dabd", "src/itsdangerous/url_safe.py",
        'if payload.startswith(b"."):', 'if payload.startswith(b","): ',
        "Fix URLSafeSerializerMixin.load_payload() so compressed URL-safe payloads are recognized and round-trip. "
        "Use the maintained tests to diagnose it and make the smallest source-only repair. Do not edit tests.",
    ),
    Case(
        "iniconfig-key-whitespace", "https://github.com/pytest-dev/iniconfig.git",
        "a9e0ad14bcd0d9d3b68d2b699ce38399cbe64fdd", "src/iniconfig/_parse.py",
        "key_name = name.strip()", "key_name = name",
        "Fix INI parsing so leading and trailing whitespace around key names is stripped, including Unicode whitespace, "
        "as the maintained tests require. Make the smallest source-only repair. Do not edit tests.",
    ),
    Case(
        "markupsafe-escape-proxy", "https://github.com/pallets/markupsafe.git",
        "4e111271b1494995708a9e878a10829b5e10c7ba", "src/markupsafe/__init__.py",
        "if type(s) is str:", "if isinstance(s, str):",
        "Fix escape() so it handles plain strings efficiently while still safely handling proxy objects that claim "
        "to be strings through __class__. Follow the maintained tests and make the smallest source-only repair. "
        "Do not edit tests.",
    ),
)


def checkout(case: Case, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    commands = (
        ["git", "init", "-q", str(path)],
        ["git", "-C", str(path), "remote", "add", "origin", case.url],
        ["git", "-C", str(path), "fetch", "-q", "--depth", "1", "origin", case.commit],
        ["git", "-C", str(path), "checkout", "-q", "--detach", "FETCH_HEAD"],
    )
    for command in commands:
        subprocess.run(command, check=True, timeout=180)
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if actual != case.commit:
        raise RuntimeError(f"{case.name}: unexpected checkout {actual}")


def prepare(case: Case, checkout_dir: Path, root: Path) -> tuple[Path, Path]:
    baseline = root / "baseline"
    mutant = root / "mutant"
    import_source(str(checkout_dir), baseline)
    import_source(str(checkout_dir), mutant)
    target = mutant / case.file
    source = target.read_text(encoding="utf-8")
    if source.count(case.original) != 1:
        raise RuntimeError(f"{case.name}: mutation anchor not unique")
    target.write_text(source.replace(case.original, case.mutant, 1), encoding="utf-8", newline="\n")
    return baseline, mutant


async def run(model: str | None, output: Path, selected_case: str | None = None, fast: bool = False) -> int:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        if selected_case and case.name != selected_case:
            continue
        root = output / case.name
        checkout_dir = root / "upstream"
        checkout(case, checkout_dir)
        baseline, mutant = prepare(case, checkout_dir, root)
        baseline_check = validate_workspace(baseline, runner="docker", timeout_seconds=180)
        mutant_check = validate_workspace(mutant, runner="docker", timeout_seconds=180)
        item = {
            "name": case.name, "upstream": case.url, "commit": case.commit,
            "baseline": baseline_check.model_dump(mode="json"),
            "mutant": mutant_check.model_dump(mode="json"),
        }
        if model and baseline_check.status == "verified" and mutant_check.status == "failed":
            settings = Settings(model, output / "runs" / case.name, 0, 180, 500_000, "unused",
                                provider="ollama", validator="docker")
            if fast:
                workspace_path = settings.data_dir / "workspace"
                import_source(str(mutant), workspace_path)
                workspace = Workspace(workspace_path, max_file_bytes=settings.max_file_bytes)
                before = snapshot_bytes(workspace_path)
                response = await run_agent(
                    "Developer", DEVELOPER,
                    f"USER REQUEST\\n{case.request}\\n\\nTarget file: {case.file}\\n"
                    "Inspect the relevant implementation and tests with workspace tools, then make the smallest "
                    "source-only repair. Do not change tests, configuration, or dependencies. Do not execute commands.",
                    tools=[workspace], model=model, provider="ollama",
                    ollama_base_url=settings.ollama_base_url,
                )
                changed = changed_files(before, snapshot_bytes(workspace_path))
                source_only = bool(changed) and all(path == case.file for path in changed)
                validation = (validate_workspace(workspace_path, timeout_seconds=180, runner="docker")
                              if source_only else None)
                item["repair"] = {
                    "status": "passed" if validation and validation.status == "verified" else "failed",
                    "response": response,
                    "changed_files": changed,
                    "source_only": source_only,
                    "validation": validation.model_dump(mode="json") if validation else None,
                    "error": None if source_only else "No source edit or changes outside the allowed target file",
                }
            else:
                report = await AgentForgeEngine(settings).run(
                    case.request, source_repo=str(mutant), allow_local_execution=True,
                )
                item["repair"] = {
                    "status": report.status, "run_id": report.run_id,
                    "error": report.error,
                    "validation": report.validation.model_dump(mode="json"),
                    "artifacts": report.artifacts, "changed_files":
                        report.attempts[-1]["changed_files"] if report.attempts else [],
                }
        results.append(item)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "name": case.name, "baseline": baseline_check.status, "mutant": mutant_check.status,
            "repair": item.get("repair", {}).get("status", "not run"),
            "error": item.get("repair", {}).get("error"),
            "baseline_issues": baseline_check.issues[:2], "mutant_issues": mutant_check.issues[:2],
        }), flush=True)
    return 0 if all(
        item["baseline"]["status"] == "verified"
        and item["mutant"]["status"] == "failed"
        and (not model or item.get("repair", {}).get("status") == "passed")
        for item in results
    ) else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".agentforge-data/benchmark"))
    parser.add_argument("--model", help="Run real Ollama repairs after baseline/mutant validation")
    parser.add_argument("--fast", action="store_true", help="Use one focused Developer pass, then Docker-validate the full repository")
    parser.add_argument("--case", choices=[case.name for case in CASES], help="Run just one pinned case")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.model, args.output.resolve(), args.case, args.fast)))


if __name__ == "__main__":
    main()

