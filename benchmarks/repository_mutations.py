"""Pinned public-repository mutation benchmark; run tests only in Docker."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine
from agentforge.source import import_source
from agentforge.validation import validate_workspace


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
        "lower/upper bounds should behave as the maintained tests specify. Use search_files, "
        "read_lines, and replace_text for a minimal change. Do not edit tests.",
    ),
    Case(
        "more-itertools-take", "https://github.com/more-itertools/more-itertools.git",
        "08dca284ba5446a026afdc34067b658f9da989eb", "more_itertools/recipes.py",
        "return list(islice(iterable, n))", "return list(islice(iterable, n + 1))",
        "Fix the off-by-one regression in more_itertools/recipes.py take(). Its maintained "
        "tests specify the correct number of items. Use search_files, read_lines, and "
        "replace_text for a minimal change. Do not edit tests.",
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


async def run(model: str | None, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
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
            settings = Settings(
                model, output / "runs" / case.name, 1, 180, 500_000, "unused",
                provider="ollama", validator="docker",
            )
            report = await AgentForgeEngine(settings).run(
                case.request, source_repo=str(mutant), allow_local_execution=True,
            )
            item["repair"] = {
                "status": report.status, "run_id": report.run_id,
                "artifacts": report.artifacts, "changed_files":
                    report.attempts[-1]["changed_files"] if report.attempts else [],
            }
        results.append(item)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "name": case.name, "baseline": baseline_check.status, "mutant": mutant_check.status,
            "repair": item.get("repair", {}).get("status", "not run"),
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
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.model, args.output.resolve())))


if __name__ == "__main__":
    main()
