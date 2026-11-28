"""Typed Pydantic security models for AgntID runtime authorization and audit."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field


class AuthorizationDecision(str, Enum):
    """Outcome of a single authorization check."""

    ALLOW = "allow"
    DENY = "deny"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


class DecisionSource(str, Enum):
    """Authoritative source that produced or enforced a decision."""

    AGNTID = "agntid"
    MOCK = "mock"
    LOCAL_POLICY = "local_policy"
    LOCAL_GATE = "local_gate"
    FAIL_CLOSED = "fail_closed"


class ActionType(str, Enum):
    """Categorized coding-agent workspace action types."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    SEARCH = "search"
    LIST = "list"
    EXECUTE = "execute"


class AuthorizationRecord(BaseModel):
    """Auditable evidence record for a single attempted tool action."""

    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = ""
    task_id: str = ""
    delegation_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    intended_task: str = ""
    tool_name: str = ""
    action_type: ActionType = ActionType.READ
    target_resource: str = ""
    normalized_path: str = ""
    action_params: dict[str, Any] = Field(default_factory=dict)
    arguments_digest: str = ""

    decision: AuthorizationDecision = AuthorizationDecision.DENY
    decision_source: DecisionSource = DecisionSource.FAIL_CLOSED
    explanation: str = ""

    # Preserve AgntID/mock verdict separately from local AgentForge policy rejections.
    agntid_verdict: str = ""
    agntid_decision_source: str = ""
    local_policy_rejected: bool = False
    local_policy_reason: str = ""

    # AgntID runtime correlation & decision evidence fields (never fabricate).
    policy_name: str = ""
    intent_status: str = ""
    denial_step: str = ""
    correlation_id: str = ""
    event_id: str = ""
    snapshot_id: str = ""
    review_id: str = ""
    review_status: str = ""
    latency_ms: int = 0

    executed: bool = False
    execution_result: str = ""
    error: str | None = None


class SecuritySummary(BaseModel):
    """Structured security audit summary attached to RunReport and security reports."""

    intended_task: str = ""
    integration_status: Literal["live_agntid", "mock_simulation", "unavailable", "disabled"] = "disabled"
    task_id: str = ""
    agent_id: str = ""
    user_id: str = ""
    intent_engine_id: str = ""
    snapshot_id: str = ""
    eligible_tools: list[str] = Field(default_factory=list)
    shortlisted_tools: list[str] = Field(default_factory=list)
    extracted_constraints: list[str] = Field(default_factory=list)

    total_attempts: int = 0
    agntid_allows: int = 0
    agntid_denies: int = 0
    agntid_restrictions: int = 0
    mock_allows: int = 0
    mock_denies: int = 0
    mock_restrictions: int = 0
    local_policy_rejections: int = 0
    executed_actions: int = 0
    prevented_actions: int = 0
    integration_errors: int = 0

    records: list[AuthorizationRecord] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)

    def add_record(self, record: AuthorizationRecord) -> None:
        """Record an authorization event and update non-overlapping counters."""
        self.records.append(record)
        self.total_attempts += 1

        # Count runtime decisions strictly by authoritative source so mock
        # decisions are never presented as real AgntID decisions.
        runtime_source = record.agntid_decision_source or (
            record.decision_source.value
            if record.decision_source in (DecisionSource.AGNTID, DecisionSource.MOCK)
            else ""
        )
        verdict = record.agntid_verdict or (
            record.decision.value
            if record.decision_source in (DecisionSource.AGNTID, DecisionSource.MOCK)
            else ""
        )

        if runtime_source == DecisionSource.AGNTID.value:
            if verdict == AuthorizationDecision.ALLOW.value:
                self.agntid_allows += 1
            elif verdict == AuthorizationDecision.DENY.value:
                self.agntid_denies += 1
            elif verdict == AuthorizationDecision.REVIEW_REQUIRED.value:
                self.agntid_restrictions += 1
        elif runtime_source == DecisionSource.MOCK.value:
            if verdict == AuthorizationDecision.ALLOW.value:
                self.mock_allows += 1
            elif verdict == AuthorizationDecision.DENY.value:
                self.mock_denies += 1
            elif verdict == AuthorizationDecision.REVIEW_REQUIRED.value:
                self.mock_restrictions += 1

        if record.local_policy_rejected or record.decision_source == DecisionSource.LOCAL_POLICY:
            self.local_policy_rejections += 1

        if record.decision == AuthorizationDecision.ERROR or record.decision_source == DecisionSource.FAIL_CLOSED:
            if record.decision == AuthorizationDecision.ERROR:
                self.integration_errors += 1

        if record.executed:
            self.executed_actions += 1
        else:
            self.prevented_actions += 1


class ExperimentResult(BaseModel):
    """Single reproducible security experiment observation."""

    experiment_id: str
    category: str = ""
    name: str
    description: str
    intended_task: str
    tool_name: str
    action_type: ActionType
    target_resource: str
    expected_outcome: str
    actual_decision: AuthorizationDecision | None = None
    actual_decision_source: DecisionSource | None = None
    agntid_verdict: str = ""
    local_policy_rejected: bool = False
    actual_explanation: str = ""
    executed: bool = False
    execution_result: str = ""
    security_interpretation: str = ""
    authorization_record: AuthorizationRecord | None = None


class ExperimentSuite(BaseModel):
    """Complete collection of security experiments and summary metrics."""

    suite_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: Literal["live_agntid", "mock_simulation"] = "mock_simulation"
    experiments: list[ExperimentResult] = Field(default_factory=list)
    summary: SecuritySummary = Field(default_factory=SecuritySummary)
