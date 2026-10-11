"""Advisory assistant abstraction.

Assistants receive a bounded, read-only context and return text. They have no access to
the repository or services, so they cannot change verification states, approve criteria
or create evidence. Everything they return is a suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import Assessment, Commitment, Criterion, Evidence, Finding, Project

MAX_FIELD_CHARS = 300
MAX_ITEMS = 40
MAX_ADVICE_CHARS = 2000


@dataclass(frozen=True)
class Advice:
    text: str
    evidence_refs: tuple[str, ...]
    provider: str
    simulated: bool
    fallback_reason: str | None = None


class Assistant(Protocol):
    label: str

    def draft_follow_up(self, context: dict) -> Advice:
        """Draft a follow-up message requesting what a criterion still needs."""

    def draft_delivery_summary(self, context: dict) -> Advice:
        """Draft handover summary wording for the current delivery state."""


def _clip(value: object) -> str:
    return str(value or "")[:MAX_FIELD_CHARS]


def finding_context(
    commitment: Commitment, criterion: Criterion, finding: Finding, evidence: list[Evidence]
) -> dict:
    """Bounded context for one finding. Free-text fields are untrusted data, not instructions."""
    return {
        "commitment": _clip(commitment.title),
        "criterion_id": criterion.id,
        "criterion": _clip(criterion.text),
        "status": str(finding.status),
        "explanation": _clip(finding.explanation),
        "missing": [_clip(m) for m in finding.missing][:MAX_ITEMS],
        "next_action": _clip(finding.next_action),
        "evidence": [
            {"id": e.id, "type": str(e.type), "description": _clip(e.description)} for e in evidence[:MAX_ITEMS]
        ],
    }


def summary_context(project: Project, assessment: Assessment, titles: dict[str, str]) -> dict:
    """Bounded context for the delivery summary; `titles` maps criterion id to criterion text."""
    return {
        "project": _clip(project.name),
        "customer": _clip(project.customer),
        "completion_state": str(assessment.completion_state),
        "customer_acceptance": str(project.acceptance),
        "verified": assessment.verified,
        "total": assessment.total,
        "readiness_pct": assessment.readiness_pct,
        "outstanding": [
            {"criterion_id": f.criterion_id, "criterion": _clip(titles.get(f.criterion_id)), "status": str(f.status)}
            for f in assessment.findings
            if f.status != "VERIFIED"
        ][:MAX_ITEMS],
    }
