"""Typed result models shared by runtime, API, and reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    duration_seconds: float = 0.0
    exit_code: int | None = None
    output: str = ""


class ValidationSummary(BaseModel):
    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class RunReport(BaseModel):
    run_id: str
    request: str
    status: Literal["passed", "failed", "error"]
    started_at: datetime
    finished_at: datetime
    workspace: str
    plan: str = ""
    optional_context: dict[str, str] = Field(default_factory=dict)
    validation: ValidationSummary
    repair_rounds: int = 0
    tester_notes: list[str] = Field(default_factory=list)
    review: str = ""
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
