"""Domain models: entities, enums and verification result types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class ValidationError(ValueError):
    """Raised when user input or a requested state transition is not acceptable."""


class EvidenceType(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    DOCUMENT = "DOCUMENT"
    TEXT = "TEXT"
    TEST_RESULT = "TEST_RESULT"
    LINK = "LINK"


class EvidenceValidity(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    STALE = "STALE"
    UNVERIFIED_REFERENCE = "UNVERIFIED_REFERENCE"


class CriterionStatus(StrEnum):
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ReviewDecision(StrEnum):
    NONE = "NONE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReportedStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    REPORTED_COMPLETE = "REPORTED_COMPLETE"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    CLOSED = "CLOSED"


class CompletionState(StrEnum):
    """Internal completion state, derived only from criterion statuses."""

    NO_CRITERIA = "NO_CRITERIA"
    NOT_READY = "NOT_READY"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    INTERNALLY_VERIFIED = "INTERNALLY_VERIFIED"


class CustomerAcceptance(StrEnum):
    """Recorded explicitly by a human; never derived from readiness."""

    NOT_RECORDED = "NOT_RECORDED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


@dataclass
class Project:
    id: str
    name: str
    customer: str
    description: str = ""
    delivery_date: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    acceptance: CustomerAcceptance = CustomerAcceptance.NOT_RECORDED
    acceptance_by: str | None = None
    acceptance_at: str | None = None
    acceptance_note: str | None = None
    created_at: str = ""


@dataclass
class Commitment:
    id: str
    project_id: str
    title: str
    description: str = ""
    expected_deliverable: str = ""
    reported_status: ReportedStatus = ReportedStatus.NOT_STARTED
    due_date: str | None = None
    position: int = 0


@dataclass
class Criterion:
    """An acceptance criterion together with its required-evidence policy."""

    id: str
    commitment_id: str
    text: str
    allowed_types: list[EvidenceType]
    min_evidence: int = 1
    requires_passing_test: bool = False
    requires_human_review: bool = True
    max_age_days: int | None = None
    decision: ReviewDecision = ReviewDecision.NONE
    decision_by: str | None = None
    decision_at: str | None = None
    decision_reason: str | None = None
    decision_fingerprint: str | None = None
    position: int = 0


@dataclass
class Evidence:
    id: str
    project_id: str
    commitment_id: str
    criterion_id: str
    type: EvidenceType
    description: str
    submitted_by: str
    submitted_at: str
    sha256: str
    original_name: str | None = None
    stored_name: str | None = None
    url: str | None = None
    size_bytes: int = 0
    validation_status: str | None = None
    validation_message: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    project_id: str
    at: str
    action: str
    entity_type: str
    entity_id: str
    previous_state: str | None
    new_state: str | None
    actor: str
    reason: str | None


@dataclass(frozen=True)
class SuiteResult:
    """A parsed structured test-result record."""

    suite: str
    total: int
    passed: int
    failed: int
    skipped: int
    executed_at: datetime

    @property
    def is_passing(self) -> bool:
        return self.failed == 0 and self.passed > 0


@dataclass(frozen=True)
class EvidenceCheck:
    evidence_id: str
    type: EvidenceType
    validity: EvidenceValidity
    messages: tuple[str, ...]
    suite: SuiteResult | None = None

    @property
    def message(self) -> str:
        return " ".join(self.messages)


@dataclass(frozen=True)
class Finding:
    criterion_id: str
    commitment_id: str
    status: CriterionStatus
    explanation: str
    missing: tuple[str, ...]
    next_action: str
    checks: tuple[EvidenceCheck, ...]
    fingerprint: str
    decision_stale: bool = False


@dataclass(frozen=True)
class Assessment:
    project_id: str
    assessed_at: str
    findings: tuple[Finding, ...]
    total: int
    verified: int
    readiness_pct: float
    completion_state: CompletionState
    counts: dict[str, int] = field(default_factory=dict)

    def finding_for(self, criterion_id: str) -> Finding:
        for finding in self.findings:
            if finding.criterion_id == criterion_id:
                return finding
        raise KeyError(criterion_id)
