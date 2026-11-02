"""Fail-closed checks for the PS-05 GitHub issue workflow."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agentforge.models import ValidationCheck, ValidationSummary
from agentforge.ps05 import parse_issue_url, reproduced, run_issue
from agentforge.workspace import Workspace, WorkspaceError


def drive_without_io(coroutine):
    """Advance a coroutine whose external calls are all mocked."""
    iterator = coroutine.__await__()
    try:
        next(iterator)
    except StopIteration as result:
        return result.value
    raise AssertionError("mocked workflow unexpectedly waited for I/O")


class FakeGitHub:
    def __init__(self):
        self.calls = []

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path.endswith("/issues/5"):
            return {"title": "Broken value", "body": "Expected 2, got 0"}
        if method == "GET" and path == "/repos/acme/demo":
            return {"default_branch": "main"}
        if method == "GET" and path.endswith("/branches/main"):
            return {"commit": {"sha": "a" * 40}}
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": "b" * 40}}
        if path.endswith("/git/blobs"):
            return {"sha": "c" * 40}
        if path.endswith("/git/trees"):
            return {"sha": "d" * 40}
        if path.endswith("/git/commits"):
            return {"sha": "e" * 40}
        if path.endswith("/pulls"):
            return {"html_url": "https://github.com/acme/demo/pull/6"}
        return {}


def baseline(exit_code=1):
    return ValidationSummary(
        passed=False, status="failed",
        checks=[ValidationCheck(name="python-syntax", passed=True, exit_code=0),
                ValidationCheck(name="pytest", passed=False, exit_code=exit_code,
                                output="assert 0 == 2")],
    )


class PS05Tests(unittest.TestCase):
    def test_issue_url_excludes_pull_requests(self):
        self.assertEqual(parse_issue_url("https://github.com/acme/demo/issues/5"),
                         ("acme", "demo", 5))
        with self.assertRaises(ValueError):
            parse_issue_url("https://github.com/acme/demo/pull/5")

    def test_reproduction_requires_a_real_test_failure(self):
        self.assertTrue(reproduced(baseline()))
        self.assertFalse(reproduced(baseline(exit_code=2)))

    def test_new_regression_test_allowed_but_existing_test_protected(self):
        with TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory), allow_new_tests=True)
            workspace.write_text("tests/test_regression.py", "def test_value(): assert 2 == 2\n")
            with self.assertRaises(WorkspaceError):
                workspace.write_text("tests/test_regression.py", "def test_value(): pass\n")
            with self.assertRaises(WorkspaceError):
                workspace.write_text("tests/conftest.py", "pass\n")

    def test_no_publication_without_reproduced_baseline(self):
        with TemporaryDirectory() as directory:
            client = FakeGitHub()
            engine = SimpleNamespace(settings=SimpleNamespace(validator="docker",
                                                             validation_timeout_seconds=30))
            with patch("agentforge.ps05.verify_checkout"), patch(
                    "agentforge.ps05.validate_workspace", return_value=baseline(exit_code=2)):
                with self.assertRaisesRegex(RuntimeError, "baseline"):
                    drive_without_io(run_issue("https://github.com/acme/demo/issues/5",
                                               source_repo=directory, client=client, engine=engine))
            self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_verified_repair_creates_draft_pr(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "output"
            (source / "src").mkdir(parents=True)
            (output / "src").mkdir(parents=True)
            (output / "tests").mkdir()
            (source / "src" / "bug.py").write_text("def value(): return 0\n")
            (output / "src" / "bug.py").write_text("def value(): return 2\n")
            (output / "tests" / "test_regression.py").write_text(
                "from src.bug import value\ndef test_value(): assert value() == 2\n")
            report = SimpleNamespace(
                run_id="f" * 32, status="passed", workspace=str(output),
                validation=ValidationSummary(passed=True, status="verified", checks=[
                    ValidationCheck(name="python-syntax", passed=True, exit_code=0),
                    ValidationCheck(name="pytest", passed=True, exit_code=0)]),
                attempts=[{"changed_files": ["src/bug.py", "tests/test_regression.py"]}],
                artifacts={"markdown": "report.md"},
            )
            engine = SimpleNamespace(settings=SimpleNamespace(validator="docker",
                                                             validation_timeout_seconds=30),
                                     run=AsyncMock(return_value=report))
            client = FakeGitHub()
            with patch("agentforge.ps05.verify_checkout"), patch(
                    "agentforge.ps05.validate_workspace", return_value=baseline()):
                result = drive_without_io(run_issue("https://github.com/acme/demo/issues/5",
                                                    source_repo=str(source), client=client, engine=engine))
            self.assertEqual(result["status"], "draft_pr_created")
            pr = next(payload for method, path, payload in client.calls
                      if method == "POST" and path.endswith("/pulls"))
            self.assertTrue(pr["draft"])
            self.assertEqual(pr["base"], "main")


if __name__ == "__main__":
    unittest.main()
