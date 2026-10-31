"""Run the real Ollama model against an intentionally broken, trusted fixture."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from agentforge.config import Settings
from agentforge.engine import AgentForgeEngine


def main() -> None:
    host = os.getenv("AGENTFORGE_OLLAMA_HOST", "127.0.0.1:11435")
    model = os.getenv("AGENTFORGE_MODEL", "qwen2.5-coder:7b")
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    # The fixture makes only localhost requests; inherited SOCKS proxy settings
    # otherwise cause httpx to require an unrelated optional dependency.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["OLLAMA_NO_CLOUD"] = "1"
    server = subprocess.Popen(["ollama", "serve"], env={**os.environ, "OLLAMA_HOST": host},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://{host}/api/tags", timeout=2) as response:
                    names = [item["name"] for item in json.load(response)["models"]]
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Ollama did not become ready")
        if model not in names:
            raise RuntimeError(f"Model {model} is not installed: {names}")
        root = Path(__file__).resolve().parents[1]
        settings = Settings(model, root / ".agentforge-data" / "live", 2, 120, 500_000,
                            "unused", provider="ollama", validator="local",
                            ollama_base_url=f"http://{host}/v1/")
        report = asyncio.run(AgentForgeEngine(settings).run(
            "Fix sample.value() so the maintained test expects and receives 2. Make the smallest change.",
            source_repo=str(root / "examples" / "repair_target"),
            allow_local_execution=True))
        expected_source = (root / "examples" / "repair_target" / "sample.py").read_text(encoding="utf-8")
        verified_change = (
            report.status == "passed"
            and report.validation.status == "verified"
            and len(report.attempts) >= 1
            and report.attempts[-1]["changed_files"] == ["sample.py"]
            and "-    return 0" in report.attempts[-1]["diffs"]["sample.py"]
            and "+    return 2" in report.attempts[-1]["diffs"]["sample.py"]
            and "return 0" in expected_source
            and any(check.name == "pytest" and check.passed for check in report.validation.checks)
        )
        print(json.dumps({"status": report.status, "error": report.error,
                          "validation": report.validation.status,
                          "attempts": len(report.attempts), "verified_change": verified_change,
                          "report": report.artifacts.get("markdown")},
                         indent=2))
        if not verified_change:
            sys.exit(1)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
