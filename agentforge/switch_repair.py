"""Switch-mediated, fail-closed review of AgentForge repairs."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from agentforge.engine import AgentForgeEngine
from agentforge.source import snapshot_bytes, changed_files, _excluded
from agentforge.validation import validate_workspace


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


@dataclass(frozen=True)
class SwitchConfig:
    base_url: str
    agent_id: str
    api_key: str
    room_id: str
    approver_id: str
    source_repo: Path
    state_dir: Path

    @classmethod
    def from_env(cls) -> "SwitchConfig":
        values = {key: os.getenv(key, "").strip() for key in (
            "SWITCH_BRIDGE_URL", "SWITCH_AGENT_ID", "SWITCH_AGENT_API_KEY",
            "SWITCH_ROOM_ID", "SWITCH_APPROVER_ID", "AGENTFORGE_SOURCE_REPO")}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))
        url = urlsplit(values["SWITCH_BRIDGE_URL"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("SWITCH_BRIDGE_URL must be an HTTP(S) bridge base URL without credentials or query")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for a non-local Switch bridge")
        source = Path(values["AGENTFORGE_SOURCE_REPO"]).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ValueError("AGENTFORGE_SOURCE_REPO must be a directory")
        state = Path(os.getenv("AGENTFORGE_SWITCH_STATE_DIR") or ".agentforge-data/switch").expanduser().resolve()
        if state == source or state in source.parents or source in state.parents:
            raise ValueError("Switch state must be outside the source repository")
        return cls(values["SWITCH_BRIDGE_URL"].rstrip("/"), values["SWITCH_AGENT_ID"],
                   values["SWITCH_AGENT_API_KEY"], values["SWITCH_ROOM_ID"],
                   values["SWITCH_APPROVER_ID"], source, state)


class RepairWorkflow:
    def __init__(self, config: SwitchConfig, *, engine=None, post=None):
        self.config = config
        self.engine = engine or AgentForgeEngine()
        self.post = post
        self._decision_lock = asyncio.Lock()
        config.state_dir.mkdir(parents=True, exist_ok=True)

    def _record_path(self, repair_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", repair_id):
            raise ValueError("Invalid repair id")
        return self.config.state_dir / f"{repair_id}.json"

    def _save(self, record: dict) -> None:
        path = self._record_path(record["id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temp.replace(path)

    def _load(self, repair_id: str) -> dict:
        return json.loads(self._record_path(repair_id).read_text(encoding="utf-8"))

    async def handle(self, frame: dict) -> None:
        if frame.get("event") != "message":
            return
        data = frame.get("data") or {}
        payload = data.get("payload") or {}
        if data.get("room_id") != self.config.room_id or not payload.get("addressed"):
            return
        sender = payload.get("sender")
        body = str(payload.get("body") or "").strip()
        if not sender or len(body) > 20000 or payload.get("attachments"):
            return
        # A user must explicitly direct a repair request or decision to this agent.
        match = re.search(r"\b(approve|reject)\s+([0-9a-f]{32})\b", body, re.I)
        if match:
            await self.decide(match.group(2).lower(), match.group(1).lower(), sender)
        elif re.search(r"\brepair\s*:\s*\S", body, re.I):
            request = re.split(r"\brepair\s*:\s*", body, maxsplit=1, flags=re.I)[1]
            await self.propose(request, sender)

    async def propose(self, request: str, sender: str) -> dict:
        # The engine imports the source and writes only to its private run workspace.
        baseline = {path: data for path, data in snapshot_bytes(self.config.source_repo).items()
                    if not any(_excluded(part) for part in Path(path).parts)}
        report = await self.engine.run(request, source_repo=str(self.config.source_repo),
                                       allow_local_execution=True, max_repair_rounds=1)
        if report.status != "passed":
            await self.post(self.config.room_id, f"Repair could not be verified: {report.status}. Run {report.run_id}; {report.error or report.validation.issues}")
            return {"status": "failed", "run_id": report.run_id}
        staged = snapshot_bytes(Path(report.workspace))
        paths = changed_files(baseline, staged)
        if not paths or any(path not in staged for path in paths):
            await self.post(self.config.room_id, "No reviewable patch was produced (or a file was deleted).")
            return {"status": "failed", "run_id": report.run_id}
        repair_id = uuid.uuid4().hex
        files = {}
        diffs = []
        for path in paths:
            old = baseline.get(path)
            new = staged[path]
            if len(new) > 500_000 or path.startswith((".git/", ".env", "tests/", ".github/")) or Path(path).name.startswith("test_"):
                raise ValueError(f"Unsafe staged path: {path}")
            files[path] = {"before": digest(old), "after": digest(new)}
            diffs.extend(difflib.unified_diff((old or b"").decode("utf-8", "replace").splitlines(True),
                                              new.decode("utf-8", "replace").splitlines(True),
                                              fromfile=f"before/{path}", tofile=f"after/{path}"))
        diff = "".join(diffs)
        if len(diff) > 10000:
            await self.post(self.config.room_id, "Patch exceeds the 10,000-character chat review limit; no approval requested.")
            return {"status": "failed", "run_id": report.run_id}
        record = {"id": repair_id, "status": "pending", "requester": sender,
                  "run_id": report.run_id, "workspace": report.workspace,
                  "files": files, "diff_sha256": digest(diff.encode()), "diff": diff,
                  "validation": report.validation.model_dump(mode="json")}
        self._save(record)
        checks = "; ".join(f"{c.name}: {'PASS' if c.passed else 'FAIL'}" for c in report.validation.checks)
        await self.post(self.config.room_id,
                        f"Proposed repair {repair_id} (run {report.run_id}). Pre-approval checks: {checks}.\n"
                        f"Diff SHA-256: {record['diff_sha256']}\n```diff\n{diff[:12000]}\n```\n"
                        f"Reviewer {self.config.approver_id}: address this agent with `approve {repair_id}` or `reject {repair_id}`. "
                        "The source has not been changed.")
        return record

    async def decide(self, repair_id: str, decision: str, sender: str) -> dict:
        async with self._decision_lock:
            return await self._decide_locked(repair_id, decision, sender)

    async def _decide_locked(self, repair_id: str, decision: str, sender: str) -> dict:
        if sender != self.config.approver_id:
            await self.post(self.config.room_id, "Decision ignored: sender is not the configured reviewer.")
            return {"status": "unauthorized"}
        record = self._load(repair_id)
        if record["status"] != "pending":
            await self.post(self.config.room_id, f"Repair {repair_id} is already {record['status']}.")
            return record
        if decision == "reject":
            record["status"] = "rejected"
            self._save(record)
            await self.post(self.config.room_id, f"Repair {repair_id} rejected; source unchanged.")
            return record
        source = self.config.source_repo
        workspace = Path(record["workspace"])
        for path, hashes in record["files"].items():
            target = (source / path).resolve()
            staged = (workspace / path).resolve()
            if not target.is_relative_to(source) or not staged.is_relative_to(workspace.resolve()):
                raise ValueError("Patch path escaped repository")
            before = target.read_bytes() if target.exists() else None
            if digest(before) != hashes["before"] or digest(staged.read_bytes()) != hashes["after"]:
                await self.post(self.config.room_id, f"Repair {repair_id} blocked: source or staged patch changed. Re-propose it.")
                return {"status": "stale"}
        record["status"] = "applying"
        self._save(record)  # Prevent a replayed approval from applying twice.
        originals = {path: (source / path).read_bytes() if (source / path).exists() else None
                     for path in record["files"]}
        try:
            for path in record["files"]:
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".agentforge-tmp")
                temporary.write_bytes((workspace / path).read_bytes())
                temporary.replace(target)
        except OSError as exc:
            for path, old in originals.items():
                target = source / path
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(old)
            record["status"] = "apply_failed"
            self._save(record)
            await self.post(self.config.room_id, f"Repair {repair_id} could not be applied: {exc}. Original files restored.")
            return record
        validation = validate_workspace(source, timeout_seconds=self.engine.settings.validation_timeout_seconds,
                                        runner=self.engine.settings.validator)
        record["status"] = "applied" if validation.passed else "applied_validation_failed"
        record["post_validation"] = validation.model_dump(mode="json")
        self._save(record)
        logs = "\n".join(f"{c.name} (exit {c.exit_code}):\n{c.output[-4000:]}" for c in validation.checks)
        await self.post(self.config.room_id, f"Repair {repair_id}: {record['status']}. Diff SHA-256: {record['diff_sha256']}.\n"
                        f"Diff:\n```diff\n{record['diff'][:12000]}\n```\nTest logs:\n```\n{logs[:12000]}\n```")
        return record


class SwitchClient:
    def __init__(self, config: SwitchConfig, workflow: RepairWorkflow | None = None):
        import httpx
        self.config = config
        self.http = httpx.AsyncClient(base_url=config.base_url,
                                      headers={"Authorization": f"Bearer {config.api_key}"}, timeout=30)
        self.workflow = workflow or RepairWorkflow(config, post=self.post)
        self.connection_id = str(uuid.uuid4())
        self.cursor: int | None = None
        self.generation = None

    async def post(self, room_id: str, content: str) -> None:
        response = await self.http.post(f"/agents/{self.config.agent_id}/message",
                                        json={"room_id": room_id, "content": content})
        response.raise_for_status()

    async def run(self) -> None:
        import websockets
        base = self.config.base_url.replace("https://", "wss://").replace("http://", "ws://")
        while True:
            params = {"connection_id": self.connection_id, "rooms": self.config.room_id,
                      "filter": "addressed", "client": "agentforge", "client_version": "0.1.0"}
            if self.cursor is not None:
                params["start_from"] = str(self.cursor)
            if self.generation is not None:
                params["expected_generation"] = str(self.generation)
            url = f"{base}/agents/{self.config.agent_id}/connection/ws?{urlencode(params)}"
            try:
                async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {self.config.api_key}"}) as ws:
                    async for raw in ws:
                        frame = json.loads(raw)
                        kind = frame.get("event") or frame.get("type")
                        if kind == "connection_state":
                            self.generation = frame.get("generation")
                            self.cursor = frame.get("cursor", self.cursor)
                        elif kind == "ping":
                            await ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
                        elif kind == "gap":
                            self.cursor = frame.get("resumed_at", self.cursor)
                            await self.post(self.config.room_id, "Switch event gap detected. Please repeat any outstanding request or decision.")
                        elif kind == "evicted" and frame.get("code") == "taken_over":
                            return
                        elif kind == "refused":
                            raise RuntimeError(f"Switch refused connection: {frame.get('detail')}")
                        elif kind == "message":
                            self.cursor = max(self.cursor or 0, int(frame["id"]))
                            asyncio.create_task(self._handle_safely(frame))
            except (OSError, websockets.ConnectionClosed):
                await asyncio.sleep(2)

    async def _handle_safely(self, frame: dict) -> None:
        try:
            await self.workflow.handle(frame)
        except Exception as exc:
            await self.post(self.config.room_id, f"Repair workflow stopped: {type(exc).__name__}: {exc}")
