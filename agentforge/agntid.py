"""AgntID Python SDK integration adapter for AgentForge.

Connects AgentForge's tool execution boundary to an AgntID MCP Runtime proxy
using the official ``agntid`` SDK (``AGNTID-AI/agntid-python``, ``agntid-sdk``):

- ``AgntidMCPClient`` / ``get_tools_list`` for MCP runtime connectivity
- ``create_task`` / ``send_task_open_details`` / ``task_context`` / ``send_task_close``
  for per-prompt task attribution and ``ExecutionContext`` provenance
- ``filter_tools_for_task`` for fail-closed prompt-aware tool shortlisting
- ``wrap_client`` for ``agntid_task_id`` / ``agntid_delegation_id`` injection and
  ``__agntid_*`` response sanitization
- ``send_delegation_open`` / ``send_delegation_close`` / ``send_action_update``
  for Supervisor -> Developer subagent delegation lineage
- ``parse_decision`` / ``parse_decision_from_exception`` / ``get_last_denial``
  for structured policy, intent, and human-review evidence

In ``mock`` mode (for unit tests and explicitly labelled offline simulations),
``FakeAgntidMCPRuntime`` implements the exact AgntID MCP tool-call and response
envelope contract in-process and labels every decision as ``mock``.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Literal, Mapping, Sequence, TypeVar
from urllib.parse import urlparse
import uuid

import agntid
from agntid.context import (
    ApprovalEvidence,
    ConversationContext,
    DelegatedIntent,
    DelegationContext,
    ExecutionContext,
    ExecutionPlan,
    FrameworkInfo,
    PlannedAction,
    arguments_digest,
    extract_explicit_constraints,
)
from agntid.decision import (
    AgntidDecision,
    ReviewDecision,
    parse_decision,
    parse_decision_from_exception,
)
from agntid.denial import get_last_denial
from agntid.display import DenialResult, ToolResult, ToolResultScalar
from agntid.intent import IntentPreparationError, TaskOpenResult
from agntid.task import (
    ACTION_UPDATE_TOOL,
    DELEGATION_CLOSE_TOOL,
    DELEGATION_OPEN_TOOL,
    TASK_CLOSE_TOOL,
    TASK_OPEN_TOOL,
)

from agentforge.security_models import (
    ActionType,
    AuthorizationDecision,
    AuthorizationRecord,
    DecisionSource,
    SecuritySummary,
)
from agentforge.workspace_routes import WorkspaceRoutes
from agentforge.workspace import Workspace, WorkspaceError

logger = logging.getLogger("agentforge.agntid")

T = TypeVar("T")

CONTROL_TOOLS: frozenset[str] = frozenset(
    {
        TASK_OPEN_TOOL,
        TASK_CLOSE_TOOL,
        ACTION_UPDATE_TOOL,
        DELEGATION_OPEN_TOOL,
        DELEGATION_CLOSE_TOOL,
    }
)

WORKSPACE_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "read_lines",
    "search_files",
    "write_file",
    "replace_text",
    "replace_line",
    "list_files",
)


def classify_action(tool_name: str) -> ActionType:
    """Classify a workspace or experiment tool name into a coarse action category."""
    if tool_name in {"read_file", "read_lines"}:
        return ActionType.READ
    if tool_name == "search_files":
        return ActionType.SEARCH
    if tool_name == "list_files":
        return ActionType.LIST
    if tool_name in {"write_file", "replace_text", "replace_line"}:
        return ActionType.WRITE
    if tool_name == "delete_file":
        return ActionType.DELETE
    return ActionType.EXECUTE


@dataclass(frozen=True)
class AgntIDRuntimeConfig:
    """Configuration for AgntID integration.

    Documented AgntID environment variables:
      - ``AGNTID_MCP_URL``: AgntID Runtime MCP proxy endpoint (default ``http://localhost:8082/mcp``)
      - ``AGNTID_ACCESS_TOKEN``: Optional raw OAuth bearer token (never logged/serialized)
      - ``AGNTID_AGENT_ID``: Logical agent identifier registered with AgntID
      - ``AGNTID_USER_ID``: Trusted user/operator identifier for task attribution

    AgentForge runtime toggles:
      - ``AGENTFORGE_AGNTID_MODE``: ``off`` | ``mcp`` | ``mock``
      - ``AGENTFORGE_AGNTID_TIMEOUT_SECONDS``: Bounded timeout for runtime calls
      - ``AGENTFORGE_AGNTID_TOOL_PREFIX``: Optional prefix if workspace tools are namespaced
    """

    mode: Literal["off", "mcp", "mock"] = "off"
    mcp_url: str = "http://localhost:8082/mcp"
    agent_id: str = "agentforge-coding-agent:v1"
    user_id: str = "local-operator"
    timeout_seconds: float = 10.0
    tool_prefix: str = ""

    def validate(self) -> None:
        if self.mode not in {"off", "mcp", "mock"}:
            raise ValueError("AGENTFORGE_AGNTID_MODE must be off, mcp, or mock")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise ValueError("AGENTFORGE_AGNTID_TIMEOUT_SECONDS must be between 0 and 120")
        if self.mode == "mcp":
            parsed = urlparse(self.mcp_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("AGNTID_MCP_URL must be a valid http(s) URL")
            if parsed.username or parsed.password:
                raise ValueError("AGNTID_MCP_URL must not embed credentials")
        if not self.agent_id.strip():
            raise ValueError("AGNTID_AGENT_ID cannot be empty")
        if not self.user_id.strip():
            raise ValueError("AGNTID_USER_ID cannot be empty")

    @classmethod
    def from_env(cls) -> "AgntIDRuntimeConfig":
        mode = os.getenv("AGENTFORGE_AGNTID_MODE", "off").strip().lower()
        raw_timeout = os.getenv("AGENTFORGE_AGNTID_TIMEOUT_SECONDS", "10.0").strip()
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("AGENTFORGE_AGNTID_TIMEOUT_SECONDS must be a number") from exc
        cfg = cls(
            mode=mode,  # type: ignore[arg-type]
            mcp_url=os.getenv("AGNTID_MCP_URL", "http://localhost:8082/mcp").strip(),
            agent_id=os.getenv("AGNTID_AGENT_ID", "agentforge-coding-agent:v1").strip(),
            user_id=os.getenv("AGNTID_USER_ID", "local-operator").strip(),
            timeout_seconds=timeout_seconds,
            tool_prefix=os.getenv("AGENTFORGE_AGNTID_TOOL_PREFIX", "").strip(),
        )
        cfg.validate()
        return cfg

    @staticmethod
    def access_token_from_env() -> str | None:
        """Read optional raw OAuth token without storing it in config/metadata."""
        token = os.getenv("AGNTID_ACCESS_TOKEN", "").strip()
        if not token:
            return None
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token or None


class _LoopRunner:
    """Dedicated single-thread event loop so async MCP clients stay loop-affine."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="agntid-mcp-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        self._closed = False

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], *, timeout: float) -> T:
        if self._closed:
            raise RuntimeError("AgntID event loop runner is closed")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"AgntID MCP operation timed out after {timeout:.2f}s") from exc

    def _stop_and_cancel(self) -> None:
        for task in asyncio.all_tasks(self._loop):
            task.cancel()
        self._loop.stop()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._stop_and_cancel)
            except RuntimeError:
                pass
        self._thread.join(timeout=2.0)
        if not self._loop.is_running() and not self._loop.is_closed():
            self._loop.close()


def _extract_raw_envelope(result: Any) -> dict[str, Any] | None:
    """Extract raw JSON/structured envelope from an MCP response before stripping."""
    if isinstance(result, Mapping):
        return dict(result)
    for attr in ("structured_content", "structuredContent"):
        sc = getattr(result, attr, None)
        if isinstance(sc, Mapping):
            inner = sc.get("result", sc)
            return dict(inner) if isinstance(inner, Mapping) else dict(sc)
    for block in getattr(result, "content", None) or ():
        text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


class _EnvelopeRecordingClient:
    """Thin MCP proxy client under ``agntid.wrap_client`` to capture audit envelopes."""

    def __init__(self, inner: Any, *, timeout_seconds: float) -> None:
        self._inner = inner
        self._timeout = timeout_seconds
        self.last_envelope: dict[str, Any] | None = None
        self.last_raw_result: Any = None
        self.last_exception: BaseException | None = None

    def reset_last(self) -> None:
        self.last_envelope = None
        self.last_raw_result = None
        self.last_exception = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def list_tools(self) -> list[Any]:
        return await asyncio.wait_for(agntid.get_tools_list(self._inner), timeout=self._timeout)

    async def call_tool_async(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        self.reset_last()
        try:
            if hasattr(self._inner, "call_tool_async"):
                coro = self._inner.call_tool_async(name, arguments, **kwargs)
            elif hasattr(self._inner, "call_tool"):
                raw = self._inner.call_tool(name, arguments, **kwargs)
                if asyncio.iscoroutine(raw):
                    coro = raw
                else:
                    self.last_raw_result = raw
                    self.last_envelope = _extract_raw_envelope(raw)
                    return raw
            else:
                raise RuntimeError("Underlying MCP client lacks call_tool / call_tool_async")
            result = await asyncio.wait_for(coro, timeout=self._timeout)
            self.last_raw_result = result
            self.last_envelope = _extract_raw_envelope(result)
            return result
        except BaseException as exc:
            self.last_exception = exc
            raise


def _unwrap_tool_value(result: Any) -> Any:
    """Convert SDK ``ToolResult`` / ``ToolResultScalar`` into plain Python types."""
    if isinstance(result, ToolResultScalar):
        return result.value
    if isinstance(result, ToolResult):
        return dict(result)
    return result


def audit_action_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Strip file payloads and potential secrets from logged action parameters."""
    sanitized: dict[str, Any] = {}
    for key, value in params.items():
        if key in {"start_line", "end_line", "line"} and type(value) is int:
            sanitized[key] = value
        elif key in {"content", "old", "new", "query", "command"} and isinstance(value, str):
            sanitized[f"{key}_length"] = len(value)
    return sanitized


def _extract_audit_fields(
    envelope: Mapping[str, Any] | None,
    decision: AgntidDecision | None,
    denial_info: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Collect documented AgntID correlation/audit fields without inventing defaults."""
    fields: dict[str, Any] = {
        "policy_name": "",
        "intent_status": "",
        "denial_step": "",
        "correlation_id": "",
        "event_id": "",
        "snapshot_id": "",
        "review_id": "",
        "review_status": "",
    }
    if envelope:
        flow = envelope.get("__agntid_execution_flow")
        flow = flow if isinstance(flow, Mapping) else {}
        policy = envelope.get("__agntid_policy_decision")
        policy = policy if isinstance(policy, Mapping) else {}
        intent = policy.get("intent_validation")
        intent = intent if isinstance(intent, Mapping) else {}
        review = intent.get("review") or flow.get("review")
        review = review if isinstance(review, Mapping) else {}

        fields["event_id"] = str(flow.get("event_id") or "")
        fields["correlation_id"] = str(
            flow.get("correlation_id") or policy.get("correlation_id") or ""
        )
        fields["policy_name"] = str(policy.get("policy_name") or "")
        fields["snapshot_id"] = str(policy.get("snapshot_id") or "")
        fields["intent_status"] = str(intent.get("status") or "")
        fields["denial_step"] = str(flow.get("denial_step") or "")
        fields["review_id"] = str(review.get("review_id") or "")
        fields["review_status"] = str(review.get("status") or "")

    if decision is not None:
        fields["policy_name"] = fields["policy_name"] or decision.policy_name
        fields["correlation_id"] = fields["correlation_id"] or decision.correlation_id
        fields["intent_status"] = fields["intent_status"] or decision.intent_status
        if decision.review is not None:
            fields["review_id"] = fields["review_id"] or decision.review.review_id
            fields["review_status"] = fields["review_status"] or decision.review.status.value

    if denial_info:
        fields["policy_name"] = fields["policy_name"] or str(denial_info.get("policy_name") or "")
        fields["correlation_id"] = fields["correlation_id"] or str(denial_info.get("correlation_id") or "")
        fields["intent_status"] = fields["intent_status"] or str(denial_info.get("intent_status") or "")
        fields["denial_step"] = fields["denial_step"] or str(denial_info.get("denial_step") or "")
        review_obj = denial_info.get("review")
        if isinstance(review_obj, ReviewDecision):
            fields["review_id"] = fields["review_id"] or review_obj.review_id
            fields["review_status"] = fields["review_status"] or review_obj.status.value

    return fields


class AgntIDSession:
    """Manages one AgentForge run's AgntID MCP lifecycle and authorization checks.

    Supports both:
    - Live AgntID MCP runtime via ``agntid.AgntidMCPClient(mcp_url, access_token=...)``
    - Explicit offline/test fake MCP runtime implementing the same AgntID wire contract
    """

    def __init__(
        self,
        config: AgntIDRuntimeConfig,
        *,
        mcp_client: Any | None = None,
        client_factory: Callable[[str, str | None], Any] | None = None,
        is_mock: bool = False,
        require_envelope: bool = True,
        max_denied_retries: int = 3,
    ) -> None:
        self.config = config
        self._explicit_client = mcp_client
        self._client_factory = client_factory
        self._is_mock = is_mock or config.mode == "mock"
        self._require_envelope = require_envelope
        self._max_denied_retries = max_denied_retries

        self._loop_runner = _LoopRunner()
        self._raw_client: Any = None
        self._entered_client = False
        self._recording_client: _EnvelopeRecordingClient | None = None
        self._wrapped_client: Any = None

        self.task_id: str = ""
        self.delegation_id: str = ""
        self.task_open_result: TaskOpenResult | None = None
        self.visible_tool_names: list[str] = []
        self.shortlisted_tool_names: list[str] = []
        self.shortlisted_mcp_tools: dict[str, str] = {}
        self.intended_task: str = ""
        self.is_open: bool = False
        self.open_error: str | None = None
        self._closed: bool = False
        self._workspace_route: tuple[WorkspaceRoutes, str] | None = None
        self._denied_counts: dict[tuple[str, str], int] = {}

    def bind_workspace(self, workspace: Workspace, shared_root: Path) -> None:
        if self._closed or self.is_open or self._workspace_route is not None:
            raise RuntimeError("A workspace route must be bound before opening the AgntID task")
        routes = WorkspaceRoutes(shared_root)
        self._workspace_route = (routes, routes.register(workspace))

    @property
    def workspace_id(self) -> str | None:
        return self._workspace_route[1] if self._workspace_route is not None else None

    @property
    def decision_source(self) -> DecisionSource:
        """Authoritative decision source for this session."""
        return DecisionSource.MOCK if self._is_mock else DecisionSource.AGNTID

    @property
    def integration_status(self) -> Literal["live_agntid", "mock_simulation", "unavailable", "disabled"]:
        if self.config.mode == "off" and self._explicit_client is None:
            return "disabled"
        if self._is_mock:
            return "mock_simulation"
        if self.is_open and not self.open_error:
            return "live_agntid"
        return "unavailable"

    def _resolve_mcp_tool_name(self, canonical_name: str, candidates: Sequence[str]) -> str | None:
        """Match canonical AgentForge tool name against shortlisted runtime MCP tools."""
        if canonical_name in candidates:
            return canonical_name
        prefixed = f"{self.config.tool_prefix}{canonical_name}" if self.config.tool_prefix else canonical_name
        if prefixed in candidates:
            return prefixed
        # Also match dot/underscore suffix e.g. 'agentforge_read_file' or 'workspace.read_file'
        for name in candidates:
            if name.endswith(f"_{canonical_name}") or name.endswith(f".{canonical_name}"):
                return name
        return None

    async def _open_async(
        self,
        intended_task: str,
        *,
        plan_text: str = "",
        constraints: Sequence[str] = (),
        planned_actions: Sequence[PlannedAction] = (),
        delegate_to_agent_id: str | None = None,
    ) -> None:
        if not intended_task or not intended_task.strip():
            raise IntentPreparationError("Intended task context is missing or empty")

        self.intended_task = intended_task.strip()
        if self._explicit_client is not None:
            client = self._explicit_client
        elif self._client_factory is not None:
            token = AgntIDRuntimeConfig.access_token_from_env()
            client = self._client_factory(self.config.mcp_url, token)
        else:
            token = AgntIDRuntimeConfig.access_token_from_env()
            client = agntid.AgntidMCPClient(self.config.mcp_url, access_token=token)

        self._raw_client = client
        if hasattr(client, "__aenter__"):
            await asyncio.wait_for(client.__aenter__(), timeout=self.config.timeout_seconds)
            self._entered_client = True

        self._recording_client = _EnvelopeRecordingClient(
            client, timeout_seconds=self.config.timeout_seconds
        )
        visible_tools = await asyncio.wait_for(
            agntid.get_tools_list(client), timeout=self.config.timeout_seconds
        )
        self.visible_tool_names = [
            str(getattr(t, "name", "") or (t.get("name") if isinstance(t, Mapping) else ""))
            for t in visible_tools
            if str(getattr(t, "name", "") or (t.get("name") if isinstance(t, Mapping) else ""))
            not in CONTROL_TOOLS
        ]

        task_id = agntid.create_task(
            self.config.agent_id,
            self.config.user_id,
            self.intended_task,
        )
        self.task_id = task_id

        all_constraints = tuple(
            dict.fromkeys(
                [
                    *extract_explicit_constraints(self.intended_task),
                    *(c.strip() for c in constraints if c and c.strip()),
                ]
            )
        )[:100]
        execution_plan = (
            ExecutionPlan(
                plan_id=f"plan-{task_id[:8]}",
                actions=tuple(planned_actions),
                source="agentforge_supervisor",
            )
            if planned_actions
            else None
        )
        exec_context = ExecutionContext(
            framework=FrameworkInfo(
                name="agentforge",
                adapter_version="0.1.0",
                capabilities=("execution_plan", "delegation", "intent_shortlist"),
            ),
            turn_id=task_id,
            root_task_id=task_id,
            root_agent_id=self.config.agent_id,
            conversation=ConversationContext(
                summary=plan_text.strip()[:2048] if plan_text else "",
                active_constraints=all_constraints,
            ),
            execution_plan=execution_plan,
        )

        task_open = await asyncio.wait_for(
            agntid.send_task_open_details(
                client,
                task_id,
                self.config.agent_id,
                self.config.user_id,
                self.intended_task,
                execution_context=exec_context,
            ),
            timeout=self.config.timeout_seconds,
        )
        self.task_open_result = task_open

        shortlisted = agntid.filter_tools_for_task(visible_tools, task_open)
        raw_shortlist_names = [
            str(getattr(t, "name", "") or (t.get("name") if isinstance(t, Mapping) else ""))
            for t in shortlisted
            if str(getattr(t, "name", "") or (t.get("name") if isinstance(t, Mapping) else ""))
            not in CONTROL_TOOLS
        ]
        self.shortlisted_tool_names = raw_shortlist_names
        self.shortlisted_mcp_tools = {}
        for canonical in WORKSPACE_TOOL_NAMES:
            matched = self._resolve_mcp_tool_name(canonical, raw_shortlist_names)
            if matched:
                self.shortlisted_mcp_tools[canonical] = matched
        # Preserve non-standard / experiment tools explicitly returned by runtime shortlist.
        for name in raw_shortlist_names:
            if name not in self.shortlisted_mcp_tools.values():
                self.shortlisted_mcp_tools[name] = name

        delegation_id: str | None = None
        if delegate_to_agent_id:
            delegation_id = f"del-{uuid.uuid4().hex[:12]}"
            delegation = DelegationContext(
                delegation_id=delegation_id,
                parent_agent_id=self.config.agent_id,
                acting_agent_id=delegate_to_agent_id,
                delegated_intent=DelegatedIntent(
                    text=self.intended_task,
                    source="agentforge_developer_delegation",
                    constraints=all_constraints,
                ),
                tool_allowlist=tuple(raw_shortlist_names),
            )
            await asyncio.wait_for(
                agntid.send_delegation_open(client, task_id, delegation),
                timeout=self.config.timeout_seconds,
            )
            self.delegation_id = delegation_id

        self._wrapped_client = agntid.wrap_client(
            self._recording_client,
            task_id=task_id,
            delegation_id=delegation_id,
        )
        self.is_open = True

    def open(
        self,
        intended_task: str,
        *,
        plan_text: str = "",
        constraints: Sequence[str] = (),
        planned_actions: Sequence[PlannedAction] = (),
        delegate_to_agent_id: str | None = None,
    ) -> bool:
        """Open the AgntID task synchronously. Fails closed and records error on failure."""
        try:
            self._loop_runner.run(
                self._open_async(
                    intended_task,
                    plan_text=plan_text,
                    constraints=constraints,
                    planned_actions=planned_actions,
                    delegate_to_agent_id=delegate_to_agent_id,
                ),
                timeout=self.config.timeout_seconds + 1.0,
            )
            return True
        except Exception as exc:
            self.is_open = False
            self.open_error = type(exc).__name__
            logger.warning("AgntID session failed closed during open: %s", self.open_error)
            return False

    def populate_summary_metadata(self, summary: SecuritySummary) -> None:
        """Populate task/intent snapshot metadata on SecuritySummary."""
        summary.integration_status = self.integration_status
        summary.task_id = self.task_id
        summary.agent_id = self.config.agent_id
        summary.user_id = self.config.user_id
        summary.eligible_tools = list(self.visible_tool_names)
        summary.shortlisted_tools = list(self.shortlisted_tool_names)
        if self.task_open_result and self.task_open_result.snapshot:
            snap = self.task_open_result.snapshot
            summary.intent_engine_id = snap.engine_id
            summary.snapshot_id = snap.snapshot_id
            summary.extracted_constraints = list(snap.constraints)

    async def _invoke_async(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        intended_task: str,
        target_resource: str,
        normalized_path: str,
        run_id: str,
        approval_provider: Callable[[str, dict[str, Any], AgntidDecision], ApprovalEvidence | None] | None = None,
    ) -> tuple[AuthorizationRecord, Any]:
        """Authorize and invoke one tool call through the wrapped AgntID MCP client."""
        started = time.perf_counter()
        action_type = classify_action(tool_name)
        digest = arguments_digest({"tool": tool_name, "path": normalized_path, **arguments})
        record = AuthorizationRecord(
            run_id=run_id,
            task_id=self.task_id,
            delegation_id=self.delegation_id,
            intended_task=intended_task[:500],
            tool_name=tool_name,
            action_type=action_type,
            target_resource=target_resource,
            normalized_path=normalized_path,
            action_params=audit_action_params(arguments),
            arguments_digest=digest,
        )

        # 1. Fail closed on missing intent context or failed/unopened runtime session.
        if not intended_task or not intended_task.strip():
            record.decision = AuthorizationDecision.ERROR
            record.decision_source = DecisionSource.FAIL_CLOSED
            record.explanation = "Missing or empty intended task context; fail-closed prevents execution."
            record.error = "missing_intent_context"
            return record, None

        if not self.is_open or self._wrapped_client is None or self._recording_client is None:
            record.decision = AuthorizationDecision.ERROR
            record.decision_source = DecisionSource.FAIL_CLOSED
            reason = self.open_error or "AgntID runtime session is unavailable or closed"
            record.explanation = f"Fail-closed: {reason}"
            record.error = reason
            return record, None

        # 2. Enforce retry boundary for repeated denied operations on the same target.
        retry_key = (tool_name, normalized_path or target_resource)
        if self._denied_counts.get(retry_key, 0) >= self._max_denied_retries:
            record.decision = AuthorizationDecision.DENY
            record.decision_source = DecisionSource.LOCAL_GATE
            record.explanation = (
                f"Denied retry limit ({self._max_denied_retries}) reached for "
                f"{tool_name} on '{normalized_path or target_resource}'."
            )
            record.error = "retry_limit_exceeded"
            return record, None

        # 3. Check prompt-aware tool shortlist returned by filter_tools_for_task.
        mcp_tool_name = self.shortlisted_mcp_tools.get(tool_name)
        if mcp_tool_name is None:
            self._denied_counts[retry_key] = self._denied_counts.get(retry_key, 0) + 1
            record.decision = AuthorizationDecision.DENY
            record.decision_source = DecisionSource.LOCAL_GATE
            record.intent_status = "excluded_by_shortlist"
            record.denial_step = "intent_shortlist"
            if self.task_open_result and self.task_open_result.snapshot:
                record.snapshot_id = self.task_open_result.snapshot.snapshot_id
            record.explanation = (
                f"Tool '{tool_name}' is not in the AgntID prompt-specific shortlist "
                f"({self.shortlisted_tool_names or ['none']})."
            )
            return record, None

        # 4. Call the AgntID-wrapped MCP tool.
        call_args = dict(arguments)
        try:
            raw_result = await self._wrapped_client.call_tool_async(mcp_tool_name, call_args)
        except Exception as exc:
            # Non-denial transport/timeout/runtime failure -> FAIL CLOSED.
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            detail = (
                f"exceeded {self.config.timeout_seconds:.2f}s timeout"
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                else "runtime call failed"
            )
            record.decision = AuthorizationDecision.ERROR
            record.decision_source = DecisionSource.FAIL_CLOSED
            record.explanation = f"AgntID runtime call failed closed ({type(exc).__name__}): {detail}"
            record.error = f"{type(exc).__name__}: {detail}"
            return record, None

        record.latency_ms = int((time.perf_counter() - started) * 1000)
        envelope = self._recording_client.last_envelope

        # 5. Handle DenialResult caught by agntid.wrap_client.
        if isinstance(raw_result, DenialResult):
            denial_info = get_last_denial(clear=True) or {}
            exc = self._recording_client.last_exception
            decision = parse_decision_from_exception(exc) if exc is not None else None
            if decision is None and envelope is not None:
                decision = parse_decision(envelope)
            audit = _extract_audit_fields(envelope, decision, denial_info)
            for k, v in audit.items():
                setattr(record, k, v)

            if decision is not None and decision.waiting_for_review:
                # Never allow an agent to approve its own restricted action.
                # Only a host-supplied approval_provider can attach ApprovalEvidence.
                if approval_provider is not None:
                    approval = approval_provider(tool_name, call_args, decision)
                    if approval is not None and approval.required and approval.decision == "approved":
                        bound_approval = ApprovalEvidence(
                            required=True,
                            decision=approval.decision,
                            approver_id=approval.approver_id,
                            decision_id=approval.decision_id,
                            operation_id=approval.operation_id or (decision.review.operation_id if decision.review else ""),
                            source=approval.source or "host_approval",
                            tool_name=mcp_tool_name,
                            arguments_digest=arguments_digest(call_args),
                            issued_at=approval.issued_at,
                            expires_at=approval.expires_at,
                        )
                        try:
                            retry_result = await self._wrapped_client.call_tool_async(
                                mcp_tool_name,
                                {**call_args, "agntid_approval": bound_approval.to_dict()},
                            )
                        except Exception as retry_exc:
                            record.decision = AuthorizationDecision.ERROR
                            record.decision_source = DecisionSource.FAIL_CLOSED
                            record.explanation = "Approval retry failed closed."
                            record.error = type(retry_exc).__name__
                            return record, None
                        if not isinstance(retry_result, DenialResult):
                            retry_env = self._recording_client.last_envelope
                            retry_dec = parse_decision(retry_env) if retry_env else None
                            if retry_dec is not None and retry_dec.allowed:
                                retry_audit = _extract_audit_fields(retry_env, retry_dec, None)
                                for k, v in retry_audit.items():
                                    setattr(record, k, v)
                                record.decision = AuthorizationDecision.ALLOW
                                record.decision_source = self.decision_source
                                record.agntid_verdict = AuthorizationDecision.ALLOW.value
                                record.agntid_decision_source = self.decision_source.value
                                record.explanation = "Allowed after host-verified approval evidence."
                                record.executed = True
                                clean = _unwrap_tool_value(retry_result)
                                record.execution_result = "Tool returned a result"
                                return record, clean

                self._denied_counts[retry_key] = self._denied_counts.get(retry_key, 0) + 1
                record.decision = AuthorizationDecision.REVIEW_REQUIRED
                record.decision_source = self.decision_source
                record.agntid_verdict = AuthorizationDecision.REVIEW_REQUIRED.value
                record.agntid_decision_source = self.decision_source.value
                record.explanation = raw_result.message
                return record, None

            self._denied_counts[retry_key] = self._denied_counts.get(retry_key, 0) + 1
            record.decision = AuthorizationDecision.DENY
            record.decision_source = self.decision_source
            record.agntid_verdict = AuthorizationDecision.DENY.value
            record.agntid_decision_source = self.decision_source.value
            record.explanation = raw_result.message
            return record, None

        # 6. Validate raw envelope integrity (fail closed on malformed or contradictory responses).
        if raw_result is None:
            record.decision = AuthorizationDecision.ERROR
            record.decision_source = DecisionSource.FAIL_CLOSED
            record.explanation = "Malformed AgntID response: empty result."
            record.error = "malformed_empty_response"
            return record, None

        if isinstance(raw_result, Mapping) and "error" in raw_result:
            record.decision = AuthorizationDecision.ERROR
            record.decision_source = DecisionSource.FAIL_CLOSED
            record.explanation = "Malformed or error MCP response."
            record.error = "mcp_error_response"
            return record, None

        if self._require_envelope:
            if not isinstance(envelope, Mapping):
                record.decision = AuthorizationDecision.ERROR
                record.decision_source = DecisionSource.FAIL_CLOSED
                record.explanation = "Malformed AgntID response: missing structured JSON envelope."
                record.error = "missing_envelope"
                return record, None
            policy_block = envelope.get("__agntid_policy_decision")
            if not isinstance(policy_block, Mapping) or "allowed" not in policy_block or not isinstance(
                policy_block.get("allowed"), bool
            ):
                record.decision = AuthorizationDecision.ERROR
                record.decision_source = DecisionSource.FAIL_CLOSED
                record.explanation = "Malformed AgntID response: invalid __agntid_policy_decision."
                record.error = "malformed_policy_decision"
                return record, None

        decision = parse_decision(envelope) if envelope is not None else None
        audit = _extract_audit_fields(envelope, decision, None)
        for k, v in audit.items():
            setattr(record, k, v)

        if decision is not None and not decision.allowed:
            self._denied_counts[retry_key] = self._denied_counts.get(retry_key, 0) + 1
            record.decision = (
                AuthorizationDecision.REVIEW_REQUIRED
                if decision.waiting_for_review
                else AuthorizationDecision.DENY
            )
            record.decision_source = self.decision_source
            record.agntid_verdict = record.decision.value
            record.agntid_decision_source = self.decision_source.value
            record.explanation = decision.reason or "Denied by AgntID policy decision."
            return record, None

        clean = _unwrap_tool_value(raw_result)
        record.decision = AuthorizationDecision.ALLOW
        record.decision_source = self.decision_source
        record.agntid_verdict = AuthorizationDecision.ALLOW.value
        record.agntid_decision_source = self.decision_source.value
        record.explanation = (
            decision.reason if (decision and decision.reason) else "Allowed by runtime policy."
        )
        record.executed = True
        record.execution_result = "Tool returned a result"
        return record, clean

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        intended_task: str,
        target_resource: str,
        normalized_path: str,
        run_id: str,
        approval_provider: Callable[[str, dict[str, Any], AgntidDecision], ApprovalEvidence | None] | None = None,
    ) -> tuple[AuthorizationRecord, Any]:
        """Synchronously invoke and authorize one tool call via the AgntID session."""
        try:
            return self._loop_runner.run(
                self._invoke_async(
                    tool_name,
                    arguments,
                    intended_task=intended_task,
                    target_resource=target_resource,
                    normalized_path=normalized_path,
                    run_id=run_id,
                    approval_provider=approval_provider,
                ),
                timeout=self.config.timeout_seconds + 1.0,
            )
        except Exception as exc:
            record = AuthorizationRecord(
                run_id=run_id,
                task_id=self.task_id,
                delegation_id=self.delegation_id,
                intended_task=intended_task[:500],
                tool_name=tool_name,
                action_type=classify_action(tool_name),
                target_resource=target_resource,
                normalized_path=normalized_path,
                action_params=audit_action_params(arguments),
                arguments_digest=arguments_digest({"tool": tool_name, "path": normalized_path, **arguments}),
                decision=AuthorizationDecision.ERROR,
                decision_source=DecisionSource.FAIL_CLOSED,
                explanation=f"AgntID authorization failed closed ({type(exc).__name__}).",
                executed=False,
                error=type(exc).__name__,
            )
            return record, None

    async def _close_async(self, *, status: str = "completed", reason: str = "") -> None:
        if self._raw_client is not None:
            try:
                if self.is_open and self.task_id:
                    if self.delegation_id:
                        try:
                            await asyncio.wait_for(
                                agntid.send_delegation_close(
                                    self._raw_client,
                                    self.task_id,
                                    self.delegation_id,
                                    status=status,
                                    reason=reason or None,
                                ),
                                timeout=self.config.timeout_seconds,
                            )
                        except Exception:
                            pass
                    try:
                        await asyncio.wait_for(
                            agntid.send_task_close(self._raw_client, self.task_id),
                            timeout=self.config.timeout_seconds,
                        )
                    except Exception:
                        pass
            finally:
                if self._entered_client and hasattr(self._raw_client, "__aexit__"):
                    try:
                        await self._raw_client.__aexit__(None, None, None)
                    except Exception:
                        pass
                self._entered_client = False
                self.is_open = False

    def close(self, *, status: str = "completed", reason: str = "") -> None:
        """Close delegation, close task, and shut down the background loop."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._raw_client is not None and (self.is_open or self._entered_client):
                self._loop_runner.run(
                    self._close_async(status=status, reason=reason),
                    timeout=self.config.timeout_seconds + 1.0,
                )
        except Exception as exc:
            logger.debug("AgntID session close error (ignored): %s", exc)
        finally:
            try:
                if self._workspace_route is not None:
                    routes, workspace_id = self._workspace_route
                    routes.revoke(workspace_id)
            except (OSError, WorkspaceError):
                logger.warning("Could not remove the AgntID workspace route")
            finally:
                self._workspace_route = None
                self._loop_runner.close()


# ---------------------------------------------------------------------------
# Contract-faithful Fake AgntID MCP Runtime (offline simulations & unit tests)
# ---------------------------------------------------------------------------


@dataclass
class FakePolicyConfig:
    """Configurable mock policy behavior for offline simulation & unit tests."""

    intent_aware: bool = True
    engine_id: str = "mock-contextual-sim"
    fail_task_open: bool = False
    task_open_reason: str = "simulated_intent_engine_unavailable"
    deny_paths: tuple[str, ...] = (".env", ".pem", ".key", ".ssh", ".aws", "credentials.json", ".npmrc", ".pypirc")
    review_required_tools: tuple[str, ...] = ()
    review_required_paths: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ("delete_file", "run_command")
    allow_all_for_test: bool = False
    inject_timeout_on_call: int | None = None
    inject_malformed_on_call: int | None = None
    inject_unavailable_on_call: int | None = None
    expose_command_double: bool = False
    command_double_handler: Callable[[str], str] | None = None
    post_auth_hook: Callable[[str, dict[str, Any]], None] | None = None


def _normalize_rel(raw_path: str) -> str:
    """Normalize a relative path without stripping leading dots from dotfiles like .env."""
    norm = raw_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _derive_mock_intent(prompt: str) -> tuple[list[str], list[str], list[str]]:
    """Derive simulated intent targets/constraints from prompt text (MOCK ONLY)."""
    lowered = prompt.lower()
    constraints = list(extract_explicit_constraints(prompt))
    write_targets: list[str] = []

    if "calculator" in lowered:
        write_targets.append("calculator.py")
    if "sample" in lowered:
        write_targets.append("sample.py")
    if "unit test" in lowered or "tests" in lowered or "test_calculator" in lowered:
        write_targets.extend(["test_calculator.py", "tests/test_calculator.py"])
    if "util" in lowered or "refactor" in lowered or "supporting" in lowered:
        write_targets.append("utils.py")
    for token in re.findall(r"[A-Za-z0-9_./-]+\.py", prompt):
        norm = _normalize_rel(token)
        if norm not in write_targets:
            write_targets.append(norm)
    return write_targets, constraints, ["read_file", "read_lines", "search_files", "list_files", "write_file", "replace_text", "replace_line"]


class FakeAgntidMCPRuntime:
    """In-process fake AgntID MCP proxy implementing the real ``agntid`` wire contract.

    Used strictly for unit tests and explicitly labelled offline simulations.
    Never claims real AgntID runtime decisions.
    """

    def __init__(self, workspace: Workspace, *, policy: FakePolicyConfig | None = None) -> None:
        self.workspace = workspace
        self.policy = policy or FakePolicyConfig()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.delegations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.business_call_count: int = 0
        self.snapshot_id = f"mock-snap-{uuid.uuid4().hex[:8]}"

    async def __aenter__(self) -> "FakeAgntidMCPRuntime":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def list_tools(self) -> list[Any]:
        tools = [SimpleNamespace(name=name) for name in WORKSPACE_TOOL_NAMES]
        if self.policy.expose_command_double:
            tools.append(SimpleNamespace(name="run_command"))
        # Include control tools to verify our client strips/hides them from models!
        for ctrl in sorted(CONTROL_TOOLS):
            tools.append(SimpleNamespace(name=ctrl))
        return tools

    async def call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))

        if name == TASK_OPEN_TOOL:
            if self.policy.fail_task_open:
                return {
                    "ok": False,
                    "reason": self.policy.task_open_reason,
                    "intent": {"prepared": False, "reason": self.policy.task_open_reason},
                }
            task_id = str(arguments.get("task_id") or "")
            prompt = str(arguments.get("prompt") or "")
            targets, constraints, relevant_tools = _derive_mock_intent(prompt)
            if self.policy.expose_command_double and any(
                w in prompt.lower() for w in ("shell", "command", "run pytest")
            ):
                relevant_tools.append("run_command")
            self.tasks[task_id] = {
                "agent_id": arguments.get("agent_id", ""),
                "user_id": arguments.get("user_id", ""),
                "prompt": prompt,
                "closed": False,
                "write_targets": targets,
                "constraints": constraints,
            }
            return {
                "ok": True,
                "reason": None,
                "intent": {
                    "prepared": True,
                    "eligible_tool_count": len(WORKSPACE_TOOL_NAMES) + (1 if self.policy.expose_command_double else 0),
                    "relevant_tool_count": len(relevant_tools),
                    "no_relevant_tools": not relevant_tools,
                    "snapshot": {
                        "id": self.snapshot_id,
                        "engine_id": self.policy.engine_id,
                        "engine_version": "mock-1.0",
                        "task_id": task_id,
                        "tasks": [{"id": "t1", "instruction": prompt}],
                        "constraints": constraints,
                        "relevant_tool_ids": relevant_tools,
                        "relevant_tool_names": relevant_tools,
                    },
                },
            }

        if name == TASK_CLOSE_TOOL:
            task_id = str(arguments.get("task_id") or "")
            if task_id in self.tasks:
                self.tasks[task_id]["closed"] = True
            return {"ok": True}

        if name == DELEGATION_OPEN_TOOL:
            del_id = str(arguments.get("delegation_id") or "")
            self.delegations[del_id] = dict(arguments)
            return {"ok": True}

        if name == DELEGATION_CLOSE_TOOL:
            return {"ok": True}

        if name == ACTION_UPDATE_TOOL:
            return {"ok": True}

        # Business tool call evaluation
        self.business_call_count += 1
        if self.policy.inject_timeout_on_call == self.business_call_count:
            await asyncio.sleep(3600)
        if self.policy.inject_unavailable_on_call == self.business_call_count:
            raise ConnectionError("Simulated AgntID runtime unavailable mid-run")
        if self.policy.inject_malformed_on_call == self.business_call_count:
            return {"unexpected": "malformed_payload_without_policy_decision"}

        task_id = str(arguments.get("agntid_task_id") or "")
        task = self.tasks.get(task_id)
        corr_id = f"mock-corr-{uuid.uuid4().hex[:10]}"
        event_id = f"mock-evt-{uuid.uuid4().hex[:10]}"

        if task is None or task.get("closed"):
            payload = {
                "__agntid_error": "Task missing, expired, or closed",
                "__agntid_policy_decision": {
                    "allowed": False,
                    "reason": "Task missing, expired, or closed",
                    "policy_name": "mock-task-attribution",
                    "correlation_id": corr_id,
                    "intent_validation": {"status": "failed", "verdict": "deny"},
                },
                "__agntid_execution_flow": {
                    "event_id": event_id,
                    "correlation_id": corr_id,
                    "denial_step": "task_resolution",
                },
            }
            raise ValueError("Error calling tool: " + json.dumps(payload))

        clean_args = {
            k: v
            for k, v in arguments.items()
            if k not in {"agntid_task_id", "agntid_delegation_id", "agntid_approval"}
        }
        approval = arguments.get("agntid_approval")
        raw_path = str(clean_args.get("path", ""))
        norm_path = _normalize_rel(raw_path)

        # Bind target path prior to policy evaluation to prevent TOCTOU swaps.
        pre_resolved: Path | None = None
        if raw_path and name in WORKSPACE_TOOL_NAMES and not (name in {"list_files", "search_files"} and raw_path == "."):
            try:
                pre_resolved = self.workspace.resolve(raw_path)
            except WorkspaceError:
                pre_resolved = None

        if not self.policy.allow_all_for_test:
            # 1. Tool-level block
            if name in self.policy.deny_tools:
                return self._raise_denial(
                    corr_id, event_id, "mock-tool-policy", "policy_evaluation",
                    f"Mock policy denies '{name}' under task intent.",
                )

            # 2. Sensitive resource check
            lower_path = norm_path.lower()
            for pat in self.policy.deny_paths:
                if pat in lower_path:
                    return self._raise_denial(
                        corr_id, event_id, "mock-sensitive-resource", "intent_parsing",
                        f"Mock intent/policy denies access to sensitive path '{raw_path}' unrelated to task.",
                    )

            # 3. Review-required trigger (unless valid host approval attached)
            needs_review = name in self.policy.review_required_tools or any(
                pat in lower_path for pat in self.policy.review_required_paths
            )
            if needs_review and not (
                isinstance(approval, Mapping) and approval.get("decision") == "approved"
            ):
                payload = {
                    "__agntid_error": "Waiting for AgntID policy review",
                    "__agntid_policy_decision": {
                        "allowed": False,
                        "reason": f"Mock policy requires human review for '{name}' on '{raw_path}'.",
                        "policy_name": "mock-human-review",
                        "correlation_id": corr_id,
                        "snapshot_id": self.snapshot_id,
                        "intent_validation": {
                            "status": "review_required",
                            "verdict": "review_required",
                            "verdict_code": "APPROVAL_REQUIRED",
                            "review": {
                                "review_id": f"mock-rev-{uuid.uuid4().hex[:8]}",
                                "operation_id": corr_id,
                                "outcome": "review_required",
                                "status": "pending",
                                "resolution_code": "APPROVAL_REQUIRED",
                                "approval_source": "host_approval",
                            },
                        },
                    },
                    "__agntid_execution_flow": {
                        "event_id": event_id,
                        "correlation_id": corr_id,
                        "denial_step": "policy_evaluation",
                    },
                }
                raise ValueError("Error calling tool: " + json.dumps(payload))

            # 4. Intent scoping for write actions
            if self.policy.intent_aware and classify_action(name) == ActionType.WRITE:
                targets = task["write_targets"]
                if targets and norm_path not in targets:
                    return self._raise_denial(
                        corr_id, event_id, "mock-intent-scope", "intent_parsing",
                        f"Mock intent mismatch: write to '{norm_path}' is outside task targets {targets}.",
                    )

        if self.policy.post_auth_hook is not None:
            self.policy.post_auth_hook(name, clean_args)

        if pre_resolved is not None:
            try:
                post_resolved = self.workspace.resolve(raw_path)
                raw_candidate = self.workspace.root / raw_path.replace("\\", "/")
                if post_resolved != pre_resolved or raw_candidate.is_symlink():
                    return {
                        "__agntid_result": "LOCAL_WORKSPACE_ERROR: Target path changed between authorization and execution",
                        "__agntid_policy_decision": {
                            "allowed": True,
                            "reason": "Allowed by mock runtime policy",
                            "policy_name": "mock-allow-policy",
                            "correlation_id": corr_id,
                            "snapshot_id": self.snapshot_id,
                            "intent_validation": {"status": "matched", "verdict": "allow"},
                        },
                        "__agntid_execution_flow": {
                            "status": "success",
                            "tool_name": name,
                            "session_id": task_id,
                            "event_id": event_id,
                            "correlation_id": corr_id,
                        },
                    }
            except WorkspaceError as exc:
                return {
                    "__agntid_result": f"LOCAL_WORKSPACE_ERROR: {exc}",
                    "__agntid_policy_decision": {
                        "allowed": True,
                        "reason": "Allowed by mock runtime policy",
                        "policy_name": "mock-allow-policy",
                        "correlation_id": corr_id,
                        "snapshot_id": self.snapshot_id,
                        "intent_validation": {"status": "matched", "verdict": "allow"},
                    },
                    "__agntid_execution_flow": {
                        "status": "success",
                        "tool_name": name,
                        "session_id": task_id,
                        "event_id": event_id,
                        "correlation_id": corr_id,
                    },
                }

        # Forward to Workspace (which independently enforces local AgentForge controls!)
        try:
            output = self._execute_workspace_tool(name, clean_args)
        except WorkspaceError as exc:
            msg = str(exc)
            is_operational = any(
                marker in msg
                for marker in (
                    "File does not exist",
                    "Directory does not exist",
                    "Start line exceeds",
                    "Replacement source must occur",
                    "Replacement line is outside",
                )
            )
            prefix = "EXECUTION_ERROR: " if is_operational else "LOCAL_WORKSPACE_ERROR: "
            output = f"{prefix}{msg}"

        return {
            "__agntid_result": output,
            "__agntid_policy_decision": {
                "allowed": True,
                "reason": "Allowed by mock runtime policy",
                "policy_name": "mock-allow-policy",
                "correlation_id": corr_id,
                "snapshot_id": self.snapshot_id,
                "intent_validation": {"status": "matched", "verdict": "allow"},
            },
            "__agntid_execution_flow": {
                "status": "success",
                "tool_name": name,
                "session_id": task_id,
                "event_id": event_id,
                "correlation_id": corr_id,
            },
        }

    def _raise_denial(
        self, corr_id: str, event_id: str, policy_name: str, step: str, reason: str
    ) -> Any:
        payload = {
            "__agntid_error": reason,
            "__agntid_policy_decision": {
                "allowed": False,
                "reason": reason,
                "policy_name": policy_name,
                "correlation_id": corr_id,
                "snapshot_id": self.snapshot_id,
                "intent_validation": {
                    "status": "mismatch",
                    "verdict": "deny",
                    "mismatch_reason": reason,
                },
            },
            "__agntid_execution_flow": {
                "event_id": event_id,
                "correlation_id": corr_id,
                "denial_step": step,
            },
        }
        raise ValueError("Error calling tool: " + json.dumps(payload))

    def _execute_workspace_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "read_file":
            return self.workspace.read_text(args["path"])
        if name == "read_lines":
            return self.workspace.read_lines(args["path"], args["start_line"], args["end_line"])
        if name == "search_files":
            return self.workspace.search_files(args["query"], args.get("path", "."))
        if name == "write_file":
            return self.workspace.write_text(args["path"], args["content"])
        if name == "replace_text":
            return self.workspace.replace_text(args["path"], args["old"], args["new"])
        if name == "replace_line":
            return self.workspace.replace_line(args["path"], args["line"], args["new"])
        if name == "list_files":
            return "\n".join(self.workspace.list_files(args.get("path", ".")))
        if name == "run_command" and self.policy.command_double_handler is not None:
            return self.policy.command_double_handler(str(args.get("command", "")))
        raise WorkspaceError(f"Unknown tool: {name}")
