"""Deterministic rules: evidence validation, status precedence, readiness."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app import rules
from app.models import (
    CompletionState,
    CriterionStatus,
    EvidenceType,
    EvidenceValidity,
    ReviewDecision,
    ValidationError,
    to_iso,
)
from tests.factories import MAX_BYTES, NOW, PDF, PNG, criterion, evidence, suite_json

TEST_CRITERION = {"allowed_types": [EvidenceType.TEST_RESULT], "requires_passing_test": True}


def check(item, crit, content):
    return rules.check_evidence(item, crit, content, NOW, MAX_BYTES)


def result_evidence(evidence_id: str, content: bytes):
    return evidence(content, id=evidence_id, type=EvidenceType.TEST_RESULT, original_name="results.json",
                    stored_name=f"PRJ-1/{evidence_id}.json")


def evaluate(crit, items_with_content):
    items = [item for item, _ in items_with_content]
    checks = [check(item, crit, content) for item, content in items_with_content]
    return rules.evaluate_criterion(crit, items, checks)


# --- structured test results -------------------------------------------------

def test_parse_suite_result_accepts_valid_record():
    suite = rules.parse_suite_result(json.loads(suite_json(failed=0, total=10)), NOW)
    assert (suite.total, suite.passed, suite.failed, suite.is_passing) == (10, 10, 0, True)


@pytest.mark.parametrize("payload", [
    [],
    {},
    {"suite": "", "total": 1, "passed": 1, "failed": 0, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": "10", "passed": 10, "failed": 0, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": True, "passed": 1, "failed": 0, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": 10, "passed": -1, "failed": 11, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": 10, "passed": 5, "failed": 0, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": 0, "passed": 0, "failed": 0, "executed_at": "2026-05-01T00:00:00Z"},
    {"suite": "s", "total": 1, "passed": 1, "failed": 0},
    {"suite": "s", "total": 1, "passed": 1, "failed": 0, "executed_at": "yesterday"},
    {"suite": "s", "total": 1, "passed": 1, "failed": 0, "executed_at": "2099-01-01T00:00:00Z"},
])
def test_parse_suite_result_rejects_malformed_records(payload):
    with pytest.raises(ValidationError):
        rules.parse_suite_result(payload, NOW)


def test_all_skipped_run_is_not_passing():
    suite = rules.parse_suite_result(json.loads(suite_json(total=5, passed=0, skipped=5)), NOW)
    assert not suite.is_passing


# --- single evidence checks --------------------------------------------------

def test_valid_screenshot_is_valid_but_flagged_as_not_proof():
    result = check(evidence(PNG), criterion(), PNG)
    assert result.validity is EvidenceValidity.VALID
    assert "does not by itself prove" in result.message


def test_evidence_type_not_allowed_is_invalid():
    item = evidence(PDF, type=EvidenceType.DOCUMENT, stored_name="PRJ-1/EVD-1.pdf")
    result = check(item, criterion(), PDF)
    assert result.validity is EvidenceValidity.INVALID
    assert "not accepted" in result.message


def test_evidence_linked_to_another_commitment_is_invalid():
    result = check(evidence(PNG, commitment_id="CMT-OTHER"), criterion(), PNG)
    assert result.validity is EvidenceValidity.INVALID
    assert "different commitment" in result.message


def test_missing_file_is_invalid():
    assert check(evidence(PNG), criterion(), None).validity is EvidenceValidity.INVALID


def test_changed_content_fails_hash_check():
    result = check(evidence(PNG), criterion(), PNG + b"tampered")
    assert result.validity is EvidenceValidity.INVALID
    assert "SHA-256" in result.message


def test_content_that_does_not_match_its_extension_is_invalid():
    fake = b"MZ this is not an image"
    assert check(evidence(fake), criterion(), fake).validity is EvidenceValidity.INVALID


def test_oversized_stored_file_is_invalid():
    result = rules.check_evidence(evidence(PNG), criterion(), PNG, NOW, max_bytes=4)
    assert result.validity is EvidenceValidity.INVALID


def test_malformed_test_result_is_invalid():
    content = b'{"suite": "regression", "total": 5}'
    result = check(result_evidence("EVD-1", content), criterion(**TEST_CRITERION), content)
    assert result.validity is EvidenceValidity.INVALID


def test_link_is_an_unverified_reference():
    crit = criterion(allowed_types=[EvidenceType.SCREENSHOT, EvidenceType.LINK])
    link = evidence(None, type=EvidenceType.LINK, url="https://example.com/x", stored_name=None, original_name=None)
    assert check(link, crit, None).validity is EvidenceValidity.UNVERIFIED_REFERENCE


def test_evidence_older_than_policy_is_stale():
    crit = criterion(max_age_days=30)
    old = evidence(PNG, submitted_at=to_iso(NOW - timedelta(days=31)))
    fresh = evidence(PNG, submitted_at=to_iso(NOW - timedelta(days=29)))
    assert check(old, crit, PNG).validity is EvidenceValidity.STALE
    assert check(fresh, crit, PNG).validity is EvidenceValidity.VALID


def test_test_result_freshness_uses_execution_time_not_upload_time():
    content = suite_json(executed_at=NOW - timedelta(days=45))
    item = result_evidence("EVD-1", content)  # uploaded two hours ago
    result = check(item, criterion(max_age_days=30, **TEST_CRITERION), content)
    assert result.validity is EvidenceValidity.STALE


# --- criterion status precedence ---------------------------------------------

def test_no_evidence_is_missing_evidence():
    finding = evaluate(criterion(), [])
    assert finding.status is CriterionStatus.MISSING_EVIDENCE
    assert finding.missing and finding.explanation


def test_any_invalid_item_makes_the_criterion_invalid_even_with_a_valid_one():
    good = evidence(PNG, id="EVD-1")
    bad = evidence(PNG, id="EVD-2")
    finding = evaluate(criterion(), [(good, PNG), (bad, b"corrupted")])
    assert finding.status is CriterionStatus.INVALID_EVIDENCE
    assert "EVD-2" in finding.explanation


def test_fewer_valid_items_than_required_is_insufficient():
    finding = evaluate(criterion(min_evidence=2), [(evidence(PNG), PNG)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    assert "1 more valid evidence" in finding.missing[0]


def test_only_a_link_is_insufficient():
    crit = criterion(allowed_types=[EvidenceType.SCREENSHOT, EvidenceType.LINK])
    link = evidence(None, type=EvidenceType.LINK, url="https://example.com/x", stored_name=None)
    finding = evaluate(crit, [(link, None)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    assert any("unverified references" in gap for gap in finding.missing)


def test_stale_evidence_does_not_count():
    old = evidence(PNG, submitted_at=to_iso(NOW - timedelta(days=90)))
    finding = evaluate(criterion(max_age_days=30), [(old, PNG)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE


def test_failed_test_result_is_insufficient():
    content = suite_json(failed=3, total=42)
    finding = evaluate(criterion(**TEST_CRITERION), [(result_evidence("EVD-1", content), content)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    assert "3 failed of 42" in finding.missing[0]


def test_latest_test_run_governs():
    failed = suite_json(failed=2, executed_at=NOW - timedelta(days=2))
    passed = suite_json(failed=0, executed_at=NOW - timedelta(hours=1))
    crit = criterion(**TEST_CRITERION)
    older_failure = [(result_evidence("EVD-1", failed), failed), (result_evidence("EVD-2", passed), passed)]
    assert evaluate(crit, older_failure).status is CriterionStatus.PENDING_HUMAN_REVIEW

    newer_failure = suite_json(failed=1, executed_at=NOW - timedelta(minutes=5))
    regression = older_failure + [(result_evidence("EVD-3", newer_failure), newer_failure)]
    assert evaluate(crit, regression).status is CriterionStatus.INSUFFICIENT_EVIDENCE


def test_failing_test_result_contradicts_other_evidence_even_when_tests_are_optional():
    crit = criterion(allowed_types=[EvidenceType.SCREENSHOT, EvidenceType.TEST_RESULT])
    failed = suite_json(failed=1)
    finding = evaluate(crit, [(evidence(PNG, id="EVD-1"), PNG), (result_evidence("EVD-2", failed), failed)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE


def test_filename_or_description_claiming_passed_is_not_test_execution():
    crit = criterion(allowed_types=[EvidenceType.TEXT, EvidenceType.TEST_RESULT], requires_passing_test=True)
    note = b"all tests passed"
    item = evidence(note, type=EvidenceType.TEXT, original_name="tests_passed.txt",
                    stored_name="PRJ-1/EVD-1.txt", description="tests passed")
    finding = evaluate(crit, [(item, note)])
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    assert any("not treated as test execution" in gap for gap in finding.missing)


def test_valid_evidence_awaits_human_review():
    assert evaluate(criterion(), [(evidence(PNG), PNG)]).status is CriterionStatus.PENDING_HUMAN_REVIEW


def test_valid_evidence_without_review_requirement_is_verified():
    finding = evaluate(criterion(requires_human_review=False), [(evidence(PNG), PNG)])
    assert finding.status is CriterionStatus.VERIFIED


def _decided(decision: ReviewDecision, items, **overrides):
    crit = criterion(decision=decision, decision_by="reviewer", decision_at="2026-06-01T10:00:00+00:00",
                     decision_reason="looked at it", **overrides)
    crit.decision_fingerprint = rules.criterion_fingerprint(crit, items)
    return crit


def test_current_approval_verifies_the_criterion():
    item = evidence(PNG)
    assert evaluate(_decided(ReviewDecision.APPROVED, [item]), [(item, PNG)]).status is CriterionStatus.VERIFIED


def test_current_rejection_wins_over_pending():
    item = evidence(PNG)
    finding = evaluate(_decided(ReviewDecision.REJECTED, [item]), [(item, PNG)])
    assert finding.status is CriterionStatus.REJECTED
    assert "looked at it" in finding.explanation


def test_rejection_stands_even_when_human_review_is_optional():
    item = evidence(PNG)
    crit = _decided(ReviewDecision.REJECTED, [item], requires_human_review=False)
    assert evaluate(crit, [(item, PNG)]).status is CriterionStatus.REJECTED


def test_approval_cannot_override_failed_automated_checks():
    item = evidence(PNG)
    crit = _decided(ReviewDecision.APPROVED, [item])
    assert evaluate(crit, [(item, b"corrupted")]).status is CriterionStatus.INVALID_EVIDENCE


def test_approval_is_ignored_when_the_evidence_set_changed():
    original = evidence(PNG, id="EVD-1")
    crit = _decided(ReviewDecision.APPROVED, [original])
    added = evidence(JPEG_LIKE, id="EVD-2", stored_name="PRJ-1/EVD-2.png")
    finding = evaluate(crit, [(original, PNG), (added, JPEG_LIKE)])
    assert finding.status is CriterionStatus.PENDING_HUMAN_REVIEW
    assert finding.decision_stale


def test_approval_is_ignored_when_the_criterion_text_changed():
    item = evidence(PNG)
    crit = _decided(ReviewDecision.APPROVED, [item])
    crit.text = "Dashboard loads in under two seconds"
    assert evaluate(crit, [(item, PNG)]).status is CriterionStatus.PENDING_HUMAN_REVIEW


JPEG_LIKE = PNG + b"-second"


def test_fingerprint_is_stable_and_order_independent():
    first, second = evidence(PNG, id="EVD-1"), evidence(JPEG_LIKE, id="EVD-2")
    crit = criterion()
    assert rules.criterion_fingerprint(crit, [first, second]) == rules.criterion_fingerprint(crit, [second, first])


def test_evaluation_is_reproducible():
    item = evidence(PNG)
    assert evaluate(criterion(), [(item, PNG)]) == evaluate(criterion(), [(item, PNG)])


# --- readiness and completion ------------------------------------------------

def _findings(*statuses: CriterionStatus):
    template = evaluate(criterion(), [])
    return [template.__class__(**{**template.__dict__, "criterion_id": f"CRT-{i}", "status": status})
            for i, status in enumerate(statuses)]


def test_readiness_of_an_empty_project_is_zero_and_flagged():
    assessment = rules.summarize("PRJ-1", [], NOW)
    assert (assessment.readiness_pct, assessment.total) == (0.0, 0)
    assert assessment.completion_state is CompletionState.NO_CRITERIA


def test_readiness_is_verified_over_total():
    V, P, M = CriterionStatus.VERIFIED, CriterionStatus.PENDING_HUMAN_REVIEW, CriterionStatus.MISSING_EVIDENCE
    assessment = rules.summarize("PRJ-1", _findings(V, P, M), NOW)
    assert assessment.readiness_pct == 33.3
    assert (assessment.verified, assessment.total) == (1, 3)
    assert assessment.counts["MISSING_EVIDENCE"] == 1
    assert assessment.completion_state is CompletionState.NOT_READY


def test_completion_states():
    V, P, R = CriterionStatus.VERIFIED, CriterionStatus.PENDING_HUMAN_REVIEW, CriterionStatus.REJECTED
    assert rules.completion_state([V, V]) is CompletionState.INTERNALLY_VERIFIED
    assert rules.completion_state([V, P]) is CompletionState.AWAITING_REVIEW
    assert rules.completion_state([V, R]) is CompletionState.NOT_READY
    assert rules.summarize("PRJ-1", _findings(V, V), NOW).readiness_pct == 100.0
