"""Authorization-governed Workspace wrapping all agent tool execution through AgntID.

``GovernedWorkspace`` subclasses ``Workspace`` so both the OpenAI Agents SDK
(``build_workspace_tools``) and the direct Ollama tool loop (``_run_ollama_agent``)
pass through one single authorization boundary with no alternate execution route.

Security invariants preserved regardless of AgntID decision:
1. Path normalization & workspace containment (no absolute paths, no ``..``, no symlinks outside root).
2. Credential-path protections (``.env*``, ``.aws``, ``.ssh``, ``*.pem``, ``*.key``, etc.).
3. Maintained test and configuration protections (``tests/``, ``test_*.py``, ``*_test.py``, ``PROTECTED_FILES``).
4. File size and line-range limits.
5. Action-to-target SHA-256 binding between authorization and execution (anti-TOCTOU).
6. Fail-closed behavior whenever AgntID authorization cannot be established.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agntid.context import ApprovalEvidence, arguments_digest
from agntid.decision import AgntidDecision

from agentforge.agntid import AgntIDSession, audit_action_params, classify_action
from agentforge.security_models import (
    AuthorizationDecision,
    AuthorizationRecord,
    DecisionSource,
    SecuritySummary,
)
from agentforge.workspace import PROTECTED_FILES, Workspace, WorkspaceError


def check_local_workspace_policy(
    workspace: Workspace,
    tool_name: str,
    target: Path,
    args: dict[str, Any],
) -> str | None:
    """Evaluate AgentForge's deterministic local security policy without mutating disk.

    Operational errors (such as missing files or non-unique replacement strings) are
    deliberately NOT treated as security policy rejections so execution failures stay
    distinguishable from authorization and policy blocks.
    """
    if tool_name in {"read_file", "read_lines"}:
        if workspace._sensitive(target):
            return "Credential path cannot be read by the agent"
        if target.is_symlink():
            return "Symbolic link target cannot be read by the agent"
        if target.is_file() and target.stat().st_size > workspace.max_file_bytes:
            return "File exceeds the configured size limit"
        if tool_name == "read_lines":
            start_line = args.get("start_line")
            end_line = args.get("end_line")
            if (
                not isinstance(start_line, int)
                or isinstance(start_line, bool)
                or not isinstance(end_line, int)
                or isinstance(end_line, bool)
                or start_line < 1
                or end_line < start_line
                or end_line - start_line >= 200
            ):
                return "Line range must be 1-based and at most 200 lines"
        return None

    if tool_name == "search_files":
        query = args.get("query", "")
        if not isinstance(query, str) or not query or len(query) > 200:
            return "Search query must be 1 to 200 characters"
        if workspace._sensitive(target):
            return "Credential path cannot be searched by the agent"
        return None

    if tool_name == "list_files":
        return None

    if tool_name in {"write_file", "replace_text", "replace_line"}:
        parts = target.relative_to(workspace.root).parts
        name = target.name.lower()
        if (
            any(part.lower() in {"tests", ".git", ".github", ".venv"} for part in parts)
            or workspace._sensitive(target)
            or name in PROTECTED_FILES
            or (name.startswith("requirements") and name.endswith(".txt"))
            or (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
        ):
            return "Protected test, configuration, or credential path cannot be edited by the agent"
        if target.is_symlink():
            return "Writing through a symbolic link is not allowed"
        if tool_name == "write_file":
            content = args.get("content", "")
            if not isinstance(content, str):
                return "Content must be a string"
            if len(content.encode("utf-8")) > workspace.max_file_bytes:
                return "File exceeds the configured size limit"
        elif tool_name == "replace_text":
            old = args.get("old", "")
            new = args.get("new", "")
            if not isinstance(old, str) or not old or len(old.encode("utf-8")) > workspace.max_file_bytes:
                return "Replacement source must be nonempty and within the file size limit"
            if not isinstance(new, str):
                return "Replacement text must be a string"
            if len(new.encode("utf-8")) > workspace.max_file_bytes:
                return "File exceeds the configured size limit"
        elif tool_name == "replace_line":
            line = args.get("line")
            new = args.get("new", "")
            if (
                not isinstance(line, int)
                or isinstance(line, bool)
                or not isinstance(new, str)
                or not new.strip()
                or "\n" in new
                or "\r" in new
            ):
                return "Replacement requires a line number and one nonempty line"
        return None

    return f"Unsupported workspace tool: {tool_name}"


def _routed_receipt(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("isError"):
        return {}
    if isinstance(result.get("structuredContent"), dict):
        result = result["structuredContent"]
    if isinstance(result.get("result"), dict):
        result = result["result"]
    receipt = result.get("agentforge_workspace_result")
    return receipt if isinstance(receipt, dict) else {}


class GovernedWorkspace(Workspace):
    """``Workspace`` subclass enforcing AgntID runtime authorization on every tool call."""

    def __init__(
        self,
        workspace: Workspace,
        session: AgntIDSession,
        intended_task: str,
        *,
        run_id: str = "",
        execute_locally_on_allow: bool = False,
        approval_provider: Callable[[str, dict[str, Any], AgntidDecision], ApprovalEvidence | None] | None = None,
        post_auth_verifier: Callable[[str, Path], None] | None = None,
    ) -> None:
        super().__init__(workspace.root, max_file_bytes=workspace.max_file_bytes)
        self._inner = workspace
        self._session = session
        self.intended_task = intended_task
        self.run_id = run_id
        self._execute_locally_on_allow = execute_locally_on_allow
        self._approval_provider = approval_provider
        self._post_auth_verifier = post_auth_verifier
        self._proxy_workspace_verified: bool | None = None

        self.security_summary = SecuritySummary(intended_task=intended_task)
        self._session.populate_summary_metadata(self.security_summary)

        # Propagate runtime-shortlisted tools to the Ollama tool loop as well.
        if session.is_open and session.shortlisted_mcp_tools:
            self.ollama_tool_allowlist: set[str] | None = set(session.shortlisted_mcp_tools.keys())
        elif session.is_open:
            self.ollama_tool_allowlist = set()
        else:
            # Fail closed: expose no tools if AgntID session failed to open.
            self.ollama_tool_allowlist = set()

    def _host_read_for_diff(self, relative_path: str) -> str:
        """Host-only read used by Ollama duplicate-write tracking (never an agent tool)."""
        return self._inner.read_text(relative_path)

    def _resolve_for_tool(self, tool_name: str, raw_path: str) -> tuple[str, Path]:
        """Normalize and validate path containment before authorization."""
        if tool_name in {"search_files", "list_files"} and raw_path == ".":
            return ".", self.root
        resolved = self._inner.resolve(raw_path)
        # Check for symlink even on read/write targets when path exists.
        raw_candidate = self.root / raw_path.replace("\\", "/")
        if raw_candidate.is_symlink():
            raise WorkspaceError("Symbolic links are not allowed")
        rel = resolved.relative_to(self.root).as_posix() or "."
        return rel, resolved

    def _record_local_rejection(
        self,
        *,
        tool_name: str,
        raw_path: str,
        normalized_path: str,
        args: dict[str, Any],
        reason: str,
        agntid_verdict: str = "",
        agntid_source: str = "",
    ) -> None:
        record = AuthorizationRecord(
            run_id=self.run_id,
            task_id=self._session.task_id,
            delegation_id=self._session.delegation_id,
            intended_task=self.intended_task[:500],
            tool_name=tool_name,
            action_type=classify_action(tool_name),
            target_resource=raw_path,
            normalized_path=normalized_path,
            action_params=audit_action_params(args),
            arguments_digest=arguments_digest({"tool": tool_name, "path": normalized_path, **args}),
            decision=AuthorizationDecision.DENY,
            decision_source=DecisionSource.LOCAL_POLICY,
            agntid_verdict=agntid_verdict,
            agntid_decision_source=agntid_source,
            local_policy_rejected=True,
            local_policy_reason=reason,
            explanation=reason,
            executed=False,
        )
        self.security_summary.add_record(record)

    def _run_governed(self, tool_name: str, raw_path: str, args: dict[str, Any]) -> Any:
        """Single authorization + local-policy + anti-TOCTOU checkpoint for all tools."""
        # 1. Fail closed immediately if intended task is empty/missing.
        if not self.intended_task or not self.intended_task.strip():
            record = AuthorizationRecord(
                run_id=self.run_id,
                task_id=self._session.task_id,
                intended_task="",
                tool_name=tool_name,
                action_type=classify_action(tool_name),
                target_resource=raw_path,
                decision=AuthorizationDecision.ERROR,
                decision_source=DecisionSource.FAIL_CLOSED,
                explanation="Intended task context is missing; fail-closed prevents execution.",
                executed=False,
                error="missing_intent_context",
            )
            self.security_summary.add_record(record)
            raise WorkspaceError(record.explanation)

        # 2. Validate & normalize path containment BEFORE authorization.
        try:
            normalized_path, resolved_target = self._resolve_for_tool(tool_name, raw_path)
        except WorkspaceError as exc:
            self._record_local_rejection(
                tool_name=tool_name,
                raw_path=raw_path,
                normalized_path="",
                args=args,
                reason=str(exc),
            )
            raise

        # 3. Evaluate local AgentForge workspace policy (does NOT read/write sensitive files).
        local_reason = check_local_workspace_policy(
            self._inner, tool_name, resolved_target, {"path": normalized_path, **args}
        )
        if self._session.config.mode == "mcp":
            if local_reason is not None:
                self._record_local_rejection(
                    tool_name=tool_name, raw_path=raw_path, normalized_path=normalized_path,
                    args=args, reason=local_reason,
                )
                raise WorkspaceError(local_reason)
            if self._session.workspace_id is not None:
                self._proxy_workspace_verified = True
            if self._proxy_workspace_verified is None:
                self._proxy_workspace_verified = self._verify_proxy_workspace()
            if not self._proxy_workspace_verified:
                record = AuthorizationRecord(
                    run_id=self.run_id, task_id=self._session.task_id,
                    intended_task=self.intended_task, tool_name=tool_name,
                    action_type=classify_action(tool_name), target_resource=raw_path,
                    normalized_path=normalized_path, action_params=audit_action_params(args),
                    decision=AuthorizationDecision.ERROR, decision_source=DecisionSource.FAIL_CLOSED,
                    explanation="The proxy could not prove it serves this run's workspace.",
                    error="proxy_workspace_unverified",
                )
                self.security_summary.add_record(record)
                raise WorkspaceError(record.explanation)

        # 4. Bind the exact normalized path and arguments digest prior to AgntID call.
        bound_args = {"path": normalized_path, **args} if raw_path or tool_name not in {"list_files", "search_files"} else dict(args)
        if tool_name in {"list_files", "search_files"}:
            bound_args["path"] = normalized_path
        if self._session.workspace_id is not None:
            bound_args["workspace_id"] = self._session.workspace_id
        pre_digest = arguments_digest({"tool": tool_name, "path": normalized_path, **bound_args})

        # 5. Invoke AgntID runtime (or mock MCP runtime).
        record, result = self._session.invoke(
            tool_name,
            bound_args,
            intended_task=self.intended_task,
            target_resource=raw_path,
            normalized_path=normalized_path,
            run_id=self.run_id,
            approval_provider=self._approval_provider,
        )

        # Preserve independent local policy protection metadata even when AgntID/mock denied.
        if local_reason is not None:
            record.local_policy_rejected = True
            record.local_policy_reason = local_reason

        # 6. Handle AgntID denial, review_required, or fail-closed error.
        if record.decision != AuthorizationDecision.ALLOW:
            record.executed = False
            record.execution_result = ""
            self.security_summary.add_record(record)
            raise WorkspaceError(
                f"AgntID {record.decision.value} ({record.decision_source.value}) for "
                f"{tool_name} on '{normalized_path}': {record.explanation}"
            )

        if "workspace_id" in bound_args:
            receipt = _routed_receipt(result)
            if "error" in receipt or not isinstance(receipt.get("value"), str):
                route_error = receipt.get("error")
                record.error = (
                    route_error if route_error in ("route_unavailable", "workspace_action_failed")
                    else "routed_result_missing"
                )
                record.executed = False
                record.execution_result = ""
                if record.error != "workspace_action_failed":
                    record.decision = AuthorizationDecision.ERROR
                    record.decision_source = DecisionSource.FAIL_CLOSED
                self.security_summary.add_record(record)
                raise WorkspaceError(f"Routed workspace operation failed ({record.error})")
            result = receipt["value"]

        # 7. AgntID returned ALLOW — local AgentForge policy must NEVER be overridden!
        if local_reason is not None or (
            "workspace_id" not in bound_args
            and isinstance(result, str) and result.startswith("LOCAL_WORKSPACE_ERROR:")
        ):
            final_reason = local_reason or str(result).removeprefix("LOCAL_WORKSPACE_ERROR:").strip()
            record.local_policy_rejected = True
            record.local_policy_reason = final_reason
            record.decision = AuthorizationDecision.DENY
            record.decision_source = DecisionSource.LOCAL_POLICY
            record.explanation = (
                f"Rejected by AgentForge local policy (after runtime verdict "
                f"'{record.agntid_verdict}'): {final_reason}"
            )
            record.executed = False
            record.execution_result = ""
            self.security_summary.add_record(record)
            raise WorkspaceError(final_reason)

        # 7b. Handle operational execution errors separately from policy denials.
        if "workspace_id" not in bound_args and isinstance(result, str) and result.startswith("EXECUTION_ERROR:"):
            exec_err = result.removeprefix("EXECUTION_ERROR:").strip()
            record.executed = False
            record.execution_result = ""
            record.error = exec_err
            self.security_summary.add_record(record)
            raise WorkspaceError(exec_err)

        # 8. Anti-TOCTOU verification: ensure target path & arguments did not change.
        try:
            if self._post_auth_verifier is not None:
                self._post_auth_verifier(normalized_path, resolved_target)
            re_normalized, re_resolved = self._resolve_for_tool(tool_name, normalized_path)
            post_digest = arguments_digest({"tool": tool_name, "path": re_normalized, **bound_args})
            if re_resolved != resolved_target or post_digest != pre_digest:
                raise WorkspaceError("Target path or arguments changed between authorization and execution")
        except WorkspaceError as exc:
            record.local_policy_rejected = True
            record.local_policy_reason = str(exc)
            record.decision = AuthorizationDecision.DENY
            record.decision_source = DecisionSource.LOCAL_POLICY
            record.explanation = str(exc)
            record.executed = False
            record.execution_result = ""
            self.security_summary.add_record(record)
            raise

        # 9. Optionally execute locally if the session was an auth-only bridge.
        if self._execute_locally_on_allow:
            try:
                result = self._execute_local(tool_name, normalized_path, args)
                record.executed = True
                record.execution_result = "Tool returned a result"
            except WorkspaceError as exc:
                record.executed = False
                record.execution_result = ""
                record.error = str(exc)
                self.security_summary.add_record(record)
                raise

        self.security_summary.add_record(record)
        return result

    def _verify_proxy_workspace(self) -> bool:
        if not self._session.is_open or "read_file" not in self._session.shortlisted_mcp_tools:
            return False
        name = f".agentforge-workspace-{uuid4().hex}.txt"
        path = self.root / name
        nonce = uuid4().hex
        try:
            marker = path.open("x", encoding="utf-8")
            try:
                with marker:
                    marker.write(nonce)
                record, result = self._session.invoke(
                    "read_file", {"path": name}, intended_task=self.intended_task,
                    target_resource=name, normalized_path=name, run_id=self.run_id,
                )
            finally:
                path.unlink(missing_ok=True)
            return record.decision == AuthorizationDecision.ALLOW and result == nonce
        except OSError:
            return False

    def _execute_local(self, tool_name: str, normalized_path: str, args: dict[str, Any]) -> Any:
        if tool_name == "read_file":
            return self._inner.read_text(normalized_path)
        if tool_name == "read_lines":
            return self._inner.read_lines(normalized_path, args["start_line"], args["end_line"])
        if tool_name == "search_files":
            return self._inner.search_files(args["query"], normalized_path)
        if tool_name == "write_file":
            return self._inner.write_text(normalized_path, args["content"])
        if tool_name == "replace_text":
            return self._inner.replace_text(normalized_path, args["old"], args["new"])
        if tool_name == "replace_line":
            return self._inner.replace_line(normalized_path, args["line"], args["new"])
        if tool_name == "list_files":
            return "\n".join(self._inner.list_files(normalized_path))
        raise WorkspaceError(f"Unsupported tool: {tool_name}")

    def invoke_experimental_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a non-workspace experiment tool (e.g. ``delete_file`` or test-double ``run_command``).

        Always passes through the AgntID session and fails closed if unsupported or denied.
        """
        raw_target = str(arguments.get("path") or arguments.get("command") or ".")
        if self._session.config.mode == "mcp":
            reason = "Experimental delete and command tools are available only in the offline fixture."
            self._record_local_rejection(
                tool_name=tool_name, raw_path=raw_target, normalized_path="",
                args=arguments, reason=reason,
            )
            raise WorkspaceError(reason)
        record, result = self._session.invoke(
            tool_name,
            arguments,
            intended_task=self.intended_task,
            target_resource=raw_target,
            normalized_path=raw_target,
            run_id=self.run_id,
            approval_provider=self._approval_provider,
        )
        self.security_summary.add_record(record)
        if record.decision != AuthorizationDecision.ALLOW:
            raise WorkspaceError(
                f"AgntID {record.decision.value} ({record.decision_source.value}) for "
                f"{tool_name}: {record.explanation}"
            )
        return result

    # --- Workspace public tool interface (all 7 governed tools) ---

    def read_text(self, relative_path: str) -> str:
        return str(self._run_governed("read_file", relative_path, {}))

    def read_lines(self, relative_path: str, start_line: int, end_line: int) -> str:
        return str(
            self._run_governed(
                "read_lines",
                relative_path,
                {"start_line": start_line, "end_line": end_line},
            )
        )

    def search_files(self, query: str, relative_path: str = ".") -> str:
        return str(self._run_governed("search_files", relative_path, {"query": query}))

    def write_text(self, relative_path: str, content: str) -> str:
        return str(self._run_governed("write_file", relative_path, {"content": content}))

    def replace_text(self, relative_path: str, old: str, new: str) -> str:
        return str(
            self._run_governed("replace_text", relative_path, {"old": old, "new": new})
        )

    def replace_line(self, relative_path: str, line: int, new: str) -> str:
        return str(
            self._run_governed("replace_line", relative_path, {"line": line, "new": new})
        )

    def list_files(self, relative_path: str = ".") -> list[str]:
        raw = self._run_governed("list_files", relative_path, {})
        if isinstance(raw, list):
            return raw
        text = str(raw).strip()
        return [line for line in text.splitlines() if line] if text else []

    def snapshot(self) -> dict[str, int]:
        """Host-only metadata snapshot (never exposed as an agent tool)."""
        return self._inner.snapshot()
