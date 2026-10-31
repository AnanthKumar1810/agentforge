import json
from pathlib import Path
import pytest
from agentforge.source import import_source, snapshot_bytes, changed_files, MAX_SOURCE_FILE_BYTES
from agentforge.test_policy import select_tests
from agentforge.validation import validate_workspace

def test_missing_tests_is_unverified(tmp_path):
    (tmp_path / "code.py").write_text("x = 1")
    result = validate_workspace(tmp_path, timeout_seconds=10)
    assert result.status == "syntax_only"
    assert not result.passed

def test_failing_syntax_is_failed(tmp_path):
    (tmp_path / "code.py").write_text("def bad(:")
    assert validate_workspace(tmp_path, timeout_seconds=10).status == "failed"

def test_maintained_tests_pass(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True")
    result = validate_workspace(tmp_path, timeout_seconds=10)
    assert result.status == "verified"
    assert result.passed

def test_maintained_tests_fail(tmp_path):
    (tmp_path / "test_no.py").write_text("def test_no(): assert False")
    assert validate_workspace(tmp_path, timeout_seconds=10).status == "failed"

def test_conditional_selection_and_missing_target(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text("def test_api(): assert True")
    (tests / "test_ui.py").write_text("def test_ui(): assert True")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"suites": [
        {"when": ["agentforge/api.py"], "tests": ["tests/test_api.py"]},
        {"when": ["web/*"], "tests": ["tests/test_ui.py"]}
    ]}))
    assert select_tests(tmp_path, ["agentforge/api.py"], str(manifest)) == ["tests/test_api.py"]
    manifest.write_text(json.dumps({"suites": [{"when": ["*"], "tests": ["tests/missing.py"]}]}))
    with pytest.raises(ValueError, match="missing"):
        select_tests(tmp_path, ["code.py"], str(manifest))

def test_source_import_is_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("x = 1")
    workspace = tmp_path / "run"
    import_source(str(source), workspace)
    before = snapshot_bytes(workspace)
    (workspace / "code.py").write_text("x = 2")
    assert changed_files(before, snapshot_bytes(workspace)) == ["code.py"]
    assert (source / "code.py").read_text() == "x = 1"

def test_source_import_excludes_credentials(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text("SECRET=example")
    (source / "server.pem").write_text("private key")
    (source / ".npmrc").write_text("token=example")
    (source / "app.py").write_text("x = 1")
    destination = tmp_path / "run"
    import_source(str(source), destination)
    assert (destination / "app.py").exists()
    assert not (destination / ".env").exists()
    assert not (destination / "server.pem").exists()
    assert not (destination / ".npmrc").exists()


def test_source_too_large_rejected_before_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.bin").write_bytes(b"x" * (MAX_SOURCE_FILE_BYTES + 1))
    destination = tmp_path / "run"
    with pytest.raises(ValueError, match="import limits"):
        import_source(str(source), destination)
    assert not destination.exists()

def test_source_symlink_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    target.write_text("secret")
    try:
        (source / "link").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symbolic"):
        import_source(str(source), tmp_path / "run")
