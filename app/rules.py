"""Deterministic verification rules.

Pure functions only: the same recorded inputs and the same `now` always produce the
same result. Nothing here calls an LLM, the network or the database.

Status precedence for a criterion (first match wins):

1. MISSING_EVIDENCE       - no evidence is linked.
2. INVALID_EVIDENCE       - any linked item fails validation.
3. INSUFFICIENT_EVIDENCE  - valid items do not satisfy the evidence policy.
4. REJECTED               - a current human rejection stands.
5. PENDING_HUMAN_REVIEW   - human review is required and no current approval exists.
6. VERIFIED               - everything above is satisfied.

A human decision is "current" only while the fingerprint recorded with it matches the
criterion's present text, policy and evidence set. Otherwise it is ignored.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from app import security
from app.models import (
    Assessment,
    CompletionState,
    Criterion,
    CriterionStatus,
    Evidence,
    EvidenceCheck,
    EvidenceType,
    EvidenceValidity,
    Finding,
    ReviewDecision,
    SuiteResult,
    ValidationError,
    parse_iso,
    to_iso,
)

RULES_VERSION = "1.0"
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

TYPE_LABELS = {
    EvidenceType.SCREENSHOT: "screenshot",
    EvidenceType.DOCUMENT: "PDF document",
    EvidenceType.TEXT: "text/Markdown file",
    EvidenceType.TEST_RESULT: "structured test result",
    EvidenceType.LINK: "external link",
}


def describe_types(types: list[EvidenceType], *, countable_only: bool = False) -> str:
    """Human-readable type list. Links never count toward sufficiency, so they can be left out."""
    return ", ".join(TYPE_LABELS[t] for t in types if not (countable_only and t is EvidenceType.LINK))


def parse_suite_result(data: object, now: datetime) -> SuiteResult:
    """Validate a structured test-result record. Raises ValidationError when malformed."""
    if not isinstance(data, dict):
        raise ValidationError("Test result must be a JSON object.")
    suite = data.get("suite")
    if not isinstance(suite, str) or not suite.strip():
        raise ValidationError("Test result is missing a 'suite' name.")
    counts: dict[str, int] = {}
    for key in ("total", "passed", "failed", "skipped"):
        value = data.get(key, 0 if key == "skipped" else None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValidationError(f"Test result field '{key}' must be a non-negative integer.")
        counts[key] = value
    if counts["total"] == 0:
        raise ValidationError("Test result reports zero executed tests.")
    if counts["passed"] + counts["failed"] + counts["skipped"] != counts["total"]:
        raise ValidationError("Test result counts are inconsistent (passed + failed + skipped != total).")
    try:
        executed_at = parse_iso(str(data["executed_at"]))
    except (KeyError, ValueError):
        raise ValidationError("Test result is missing a valid ISO-8601 'executed_at' timestamp.") from None
    if executed_at > now + CLOCK_SKEW_TOLERANCE:
        raise ValidationError("Test result 'executed_at' is in the future.")
    return SuiteResult(suite=suite.strip()[:120], executed_at=executed_at, **counts)


def check_evidence(
    evidence: Evidence, criterion: Criterion, content: bytes | None, now: datetime, max_bytes: int
) -> EvidenceCheck:
    """Validate one evidence item against the criterion it is linked to."""
    problems: list[str] = []
    suite: SuiteResult | None = None

    if evidence.criterion_id != criterion.id or evidence.commitment_id != criterion.commitment_id:
        problems.append("Evidence is linked to a different commitment or criterion.")
    if evidence.type not in criterion.allowed_types:
        problems.append(
            f"Evidence type '{TYPE_LABELS[evidence.type]}' is not accepted for this criterion "
            f"(accepted: {describe_types(criterion.allowed_types)})."
        )

    if evidence.type is EvidenceType.LINK:
        if problems:
            return EvidenceCheck(evidence.id, evidence.type, EvidenceValidity.INVALID, tuple(problems))
        note = "External link recorded as an unverified reference; its content was not fetched or checked."
        return EvidenceCheck(evidence.id, evidence.type, EvidenceValidity.UNVERIFIED_REFERENCE, (note,))

    if content is None:
        problems.append("The stored evidence file is missing.")
    else:
        if len(content) > max_bytes:
            problems.append("The stored file exceeds the configured size limit.")
        if security.sha256_hex(content) != evidence.sha256:
            problems.append("Content does not match the recorded SHA-256 hash (file changed or corrupted).")
        else:
            extension = PurePosixPath(evidence.stored_name or "").suffix.lower()
            signature_problem = security.check_signature(extension, content)
            if signature_problem:
                problems.append(signature_problem)
            elif evidence.type is EvidenceType.TEST_RESULT:
                try:
                    suite = parse_suite_result(json.loads(content.decode("utf-8")), now)
                except ValidationError as exc:
                    problems.append(str(exc))

    if problems:
        return EvidenceCheck(evidence.id, evidence.type, EvidenceValidity.INVALID, tuple(problems))

    reference_time = suite.executed_at if suite else parse_iso(evidence.submitted_at)
    if criterion.max_age_days is not None and now - reference_time > timedelta(days=criterion.max_age_days):
        age = (now - reference_time).days
        message = f"Evidence is {age} days old; this criterion accepts evidence up to {criterion.max_age_days} days old."
        return EvidenceCheck(evidence.id, evidence.type, EvidenceValidity.STALE, (message,), suite)

    notes = ["File integrity and type confirmed."]
    if suite:
        outcome = "passed" if suite.is_passing else "FAILED"
        notes.append(
            f"Test suite '{suite.suite}' {outcome}: {suite.passed} passed, {suite.failed} failed, "
            f"{suite.skipped} skipped of {suite.total}."
        )
    elif evidence.type is EvidenceType.SCREENSHOT:
        notes.append("A screenshot does not by itself prove the functionality works; a human reviewer decides.")
    else:
        notes.append("The content's truthfulness is not assessed automatically.")
    return EvidenceCheck(evidence.id, evidence.type, EvidenceValidity.VALID, tuple(notes), suite)


def criterion_fingerprint(criterion: Criterion, evidence: list[Evidence]) -> str:
    """Hash of everything a human decision depends on: criterion text, policy and evidence set."""
    payload = {
        "text": criterion.text,
        "allowed_types": sorted(criterion.allowed_types),
        "min_evidence": criterion.min_evidence,
        "requires_passing_test": criterion.requires_passing_test,
        "requires_human_review": criterion.requires_human_review,
        "max_age_days": criterion.max_age_days,
        "evidence": sorted((e.id, e.sha256) for e in evidence),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _policy_gaps(criterion: Criterion, checks: list[EvidenceCheck]) -> list[str]:
    valid = [c for c in checks if c.validity is EvidenceValidity.VALID]
    gaps: list[str] = []
    if len(valid) < criterion.min_evidence:
        gaps.append(
            f"{criterion.min_evidence - len(valid)} more valid evidence item(s) of type "
            f"{describe_types(criterion.allowed_types, countable_only=True)} "
            f"(have {len(valid)} of {criterion.min_evidence})."
        )
    # The most recent valid test run governs: a later passing run supersedes an earlier failure.
    runs = sorted((c.suite for c in valid if c.suite), key=lambda s: s.executed_at)
    if runs and not runs[-1].is_passing:
        latest = runs[-1]
        gaps.append(
            f"The latest test result ('{latest.suite}', {to_iso(latest.executed_at)}) reports "
            f"{latest.failed} failed of {latest.total} tests; a passing run is required."
        )
    elif criterion.requires_passing_test and not runs:
        gaps.append(
            "A structured test-result record showing a passing run. File names and descriptions "
            "are not treated as test execution."
        )
    if gaps:
        if any(c.validity is EvidenceValidity.UNVERIFIED_REFERENCE for c in checks):
            gaps.append("External links are unverified references and do not count as evidence on their own.")
        if any(c.validity is EvidenceValidity.STALE for c in checks):
            gaps.append("Stale evidence does not count; submit a current artifact.")
    return gaps


def evaluate_criterion(criterion: Criterion, evidence: list[Evidence], checks: list[EvidenceCheck]) -> Finding:
    """Apply the documented precedence policy to one criterion."""
    fingerprint = criterion_fingerprint(criterion, evidence)
    decision = criterion.decision
    decision_stale = decision is not ReviewDecision.NONE and criterion.decision_fingerprint != fingerprint
    if decision_stale:
        decision = ReviewDecision.NONE

    missing: list[str] = []
    invalid = [c for c in checks if c.validity is EvidenceValidity.INVALID]
    if not checks:
        status = CriterionStatus.MISSING_EVIDENCE
        explanation = "No evidence is linked to this criterion."
        accepted = describe_types(criterion.allowed_types, countable_only=True)
        missing = [f"At least {criterion.min_evidence} evidence item(s) of type {accepted}."]
        next_action = "Attach evidence for this criterion in the Evidence Workspace."
    elif invalid:
        status = CriterionStatus.INVALID_EVIDENCE
        details = " ".join(f"[{c.evidence_id}] {c.message}" for c in invalid)
        explanation = f"{len(invalid)} evidence item(s) failed validation. {details}"
        missing = ["Every linked evidence item must pass validation."]
        next_action = "Remove or replace the invalid evidence, then verify again."
    elif gaps := _policy_gaps(criterion, checks):
        status = CriterionStatus.INSUFFICIENT_EVIDENCE
        explanation = "Evidence was submitted but does not satisfy this criterion's evidence policy."
        missing = gaps
        next_action = "Supply the missing evidence listed, then verify again."
    elif decision is ReviewDecision.REJECTED:
        status = CriterionStatus.REJECTED
        explanation = f"Rejected by reviewer '{criterion.decision_by}': {criterion.decision_reason}"
        next_action = "Address the reviewer's reason and submit updated evidence; this reopens the review."
    elif criterion.requires_human_review and decision is not ReviewDecision.APPROVED:
        status = CriterionStatus.PENDING_HUMAN_REVIEW
        explanation = (
            "Automated checks passed. A human reviewer must confirm that the evidence actually "
            "demonstrates this criterion."
        )
        if decision_stale:
            explanation += " An earlier review decision no longer applies because the evidence or criterion changed."
        next_action = "A reviewer should inspect the evidence and approve or reject this criterion."
    else:
        status = CriterionStatus.VERIFIED
        if criterion.requires_human_review:
            explanation = (
                f"Automated checks passed and reviewer '{criterion.decision_by}' approved on {criterion.decision_at}."
            )
        else:
            explanation = "Automated checks passed; this criterion is configured without human review."
        next_action = "None."

    return Finding(
        criterion_id=criterion.id,
        commitment_id=criterion.commitment_id,
        status=status,
        explanation=explanation,
        missing=tuple(missing),
        next_action=next_action,
        checks=tuple(checks),
        fingerprint=fingerprint,
        decision_stale=decision_stale,
    )


def completion_state(statuses: list[CriterionStatus]) -> CompletionState:
    if not statuses:
        return CompletionState.NO_CRITERIA
    if all(s is CriterionStatus.VERIFIED for s in statuses):
        return CompletionState.INTERNALLY_VERIFIED
    if all(s in (CriterionStatus.VERIFIED, CriterionStatus.PENDING_HUMAN_REVIEW) for s in statuses):
        return CompletionState.AWAITING_REVIEW
    return CompletionState.NOT_READY


def summarize(project_id: str, findings: list[Finding], now: datetime) -> Assessment:
    """Readiness = verified criteria / total criteria x 100. Every criterion is required and equally weighted."""
    statuses = [f.status for f in findings]
    counts = Counter(str(s) for s in statuses)
    verified = counts[CriterionStatus.VERIFIED]
    total = len(findings)
    return Assessment(
        project_id=project_id,
        assessed_at=to_iso(now),
        findings=tuple(findings),
        total=total,
        verified=verified,
        readiness_pct=round(verified / total * 100, 1) if total else 0.0,
        completion_state=completion_state(statuses),
        counts={str(s): counts[str(s)] for s in CriterionStatus},
    )
