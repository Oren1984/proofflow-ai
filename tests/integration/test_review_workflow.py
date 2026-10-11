"""Human review transitions, invalidation of stale decisions and customer acceptance."""

from __future__ import annotations

import pytest

from app.models import (
    CompletionState,
    CriterionStatus,
    CustomerAcceptance,
    EvidenceType,
    ReportedStatus,
    ReviewDecision,
    ValidationError,
)
from tests.factories import PDF, PNG

OTHER_PNG = PNG + b"-another-screenshot"


def status(services, seeded) -> CriterionStatus:
    return services.verification.assess(seeded.project.id).finding_for(seeded.criterion.id).status


def actions(services, seeded) -> list[str]:
    return [entry.action for entry in services.repo.list_audit(seeded.project.id)]


@pytest.fixture
def pending(services, seeded):
    """Criterion with valid evidence, waiting for a human decision."""
    services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    assert status(services, seeded) is CriterionStatus.PENDING_HUMAN_REVIEW
    return seeded


@pytest.fixture
def approved(services, pending):
    services.approvals.approve(pending.criterion.id, "Dana (QA)", "Checked against staging")
    assert status(services, pending) is CriterionStatus.VERIFIED
    return pending


def test_approval_is_persisted_with_reviewer_label_and_history(services, approved):
    criterion = services.repo.get_criterion(approved.criterion.id)
    assert (criterion.decision, criterion.decision_by) == (ReviewDecision.APPROVED, "Dana (QA)")
    assert criterion.decision_at and criterion.decision_fingerprint
    entry = services.repo.list_audit(approved.project.id, approved.criterion.id)[-1]
    assert (entry.action, entry.previous_state, entry.new_state, entry.actor, entry.reason) == (
        "APPROVED", "PENDING_HUMAN_REVIEW", "VERIFIED", "Dana (QA)", "Checked against staging")
    assert entry.at and entry.entity_type == "criterion"


def test_cannot_approve_without_evidence(services, seeded):
    with pytest.raises(ValidationError, match="MISSING_EVIDENCE"):
        services.approvals.approve(seeded.criterion.id, "Dana")
    assert status(services, seeded) is CriterionStatus.MISSING_EVIDENCE
    assert services.repo.list_audit(seeded.project.id) == []


def test_cannot_approve_when_automated_checks_fail(services, pending):
    evidence = services.repo.list_evidence(criterion_id=pending.criterion.id)[0]
    (services.settings.evidence_dir / evidence.stored_name).write_bytes(b"tampered")
    with pytest.raises(ValidationError, match="INVALID_EVIDENCE"):
        services.approvals.approve(pending.criterion.id, "Dana")


def test_cannot_approve_twice(services, approved):
    with pytest.raises(ValidationError, match="VERIFIED"):
        services.approvals.approve(approved.criterion.id, "Dana")


def test_reviewer_label_is_required(services, pending):
    with pytest.raises(ValidationError, match="Reviewer label"):
        services.approvals.approve(pending.criterion.id, "  ")


def test_rejection_requires_a_reason_and_is_recorded(services, pending):
    with pytest.raises(ValidationError, match="reason"):
        services.approvals.reject(pending.criterion.id, "Dana", "")
    services.approvals.reject(pending.criterion.id, "Dana", "Screenshot shows the login page, not the dashboard")
    finding = services.verification.assess(pending.project.id).finding_for(pending.criterion.id)
    assert finding.status is CriterionStatus.REJECTED and "login page" in finding.explanation
    with pytest.raises(ValidationError, match="REJECTED"):
        services.approvals.approve(pending.criterion.id, "Dana")


def test_cannot_reject_a_criterion_already_blocked_by_automated_findings(services, seeded):
    with pytest.raises(ValidationError, match="Cannot reject"):
        services.approvals.reject(seeded.criterion.id, "Dana", "No evidence")


def test_an_approved_criterion_can_be_rejected_later(services, approved):
    services.approvals.reject(approved.criterion.id, "Lee", "Found a defect during handover")
    assert status(services, approved) is CriterionStatus.REJECTED


def test_revoke_returns_the_criterion_to_review(services, approved):
    with pytest.raises(ValidationError, match="reason"):
        services.approvals.revoke(approved.criterion.id, "Dana", "")
    services.approvals.revoke(approved.criterion.id, "Dana", "Approved the wrong item")
    assert status(services, approved) is CriterionStatus.PENDING_HUMAN_REVIEW
    assert services.repo.get_criterion(approved.criterion.id).decision is ReviewDecision.NONE
    entry = services.repo.list_audit(approved.project.id, approved.criterion.id)[-1]
    assert (entry.action, entry.previous_state, entry.new_state) == ("REVOKED", "VERIFIED", "PENDING_HUMAN_REVIEW")


def test_cannot_revoke_when_there_is_no_decision(services, pending):
    with pytest.raises(ValidationError, match="no review decision"):
        services.approvals.revoke(pending.criterion.id, "Dana", "Nothing to revoke")


def test_rejection_can_be_revoked(services, pending):
    services.approvals.reject(pending.criterion.id, "Dana", "Wrong page")
    services.approvals.revoke(pending.criterion.id, "Dana", "Rejected by mistake")
    assert status(services, pending) is CriterionStatus.PENDING_HUMAN_REVIEW


def test_adding_evidence_invalidates_an_approval(services, approved):
    services.evidence.add_file(approved.criterion.id, "second.png", OTHER_PNG, "Another view", "alice")
    assert status(services, approved) is CriterionStatus.PENDING_HUMAN_REVIEW
    assert services.repo.get_criterion(approved.criterion.id).decision is ReviewDecision.NONE
    entry = [e for e in services.repo.list_audit(approved.project.id) if e.action == "DECISION_INVALIDATED"][-1]
    assert (entry.previous_state, entry.new_state, entry.actor) == ("APPROVED", "PENDING_HUMAN_REVIEW", "system")
    assert "was added" in entry.reason


def test_removing_evidence_invalidates_an_approval(services, approved):
    evidence = services.repo.list_evidence(criterion_id=approved.criterion.id)[0]
    services.evidence.remove(evidence.id, "alice", "Superseded")
    assert status(services, approved) is CriterionStatus.MISSING_EVIDENCE
    assert services.repo.get_criterion(approved.criterion.id).decision is ReviewDecision.NONE
    assert actions(services, approved)[-2:] == ["EVIDENCE_REMOVED", "DECISION_INVALIDATED"]


def test_replacing_evidence_reopens_a_rejection(services, pending):
    services.approvals.reject(pending.criterion.id, "Dana", "Wrong page")
    services.evidence.add_file(pending.criterion.id, "right.png", OTHER_PNG, "Correct page", "alice")
    assert status(services, pending) is CriterionStatus.PENDING_HUMAN_REVIEW


def test_editing_the_criterion_invalidates_an_approval(services, approved):
    services.projects.update_criterion(
        approved.criterion.id, text="Dashboard loads within two seconds", allowed_types=[EvidenceType.SCREENSHOT],
        min_evidence=1, requires_passing_test=False, requires_human_review=True, max_age_days=None)
    assert status(services, approved) is CriterionStatus.PENDING_HUMAN_REVIEW
    assert "DECISION_INVALIDATED" in actions(services, approved)


def test_saving_a_criterion_without_changes_keeps_the_approval(services, approved):
    services.projects.update_criterion(
        approved.criterion.id, text="Dashboard loads", allowed_types=[EvidenceType.SCREENSHOT],
        min_evidence=1, requires_passing_test=False, requires_human_review=True, max_age_days=None)
    assert status(services, approved) is CriterionStatus.VERIFIED


def test_a_stale_approval_cannot_be_reused(services, approved):
    """Even if the evidence table is changed behind the service layer, the old approval does not carry over."""
    criterion = services.repo.get_criterion(approved.criterion.id)
    evidence = services.repo.list_evidence(criterion_id=criterion.id)[0]
    services.repo.delete_evidence(evidence.id)
    replacement = services.evidence.add_file(criterion.id, "swapped.png", OTHER_PNG, "Swapped", "mallory")
    criterion.decision, criterion.decision_by = ReviewDecision.APPROVED, "Dana (QA)"  # replay the old decision
    services.repo.update_criterion(criterion)
    finding = services.verification.assess(approved.project.id).finding_for(criterion.id)
    assert replacement.id != evidence.id
    assert finding.status is CriterionStatus.PENDING_HUMAN_REVIEW and finding.decision_stale


def test_reopening_a_commitment_clears_decisions_and_reported_status(services, approved):
    commitment = services.repo.get_commitment(approved.commitment.id)
    commitment.reported_status = ReportedStatus.REPORTED_COMPLETE
    services.repo.update_commitment(commitment)
    with pytest.raises(ValidationError, match="reason"):
        services.approvals.reopen_commitment(approved.commitment.id, "Dana", "")
    services.approvals.reopen_commitment(approved.commitment.id, "Dana", "Customer reported a regression")
    assert status(services, approved) is CriterionStatus.PENDING_HUMAN_REVIEW
    assert services.repo.get_commitment(approved.commitment.id).reported_status is ReportedStatus.IN_PROGRESS
    assert actions(services, approved)[-2:] == ["REVOKED", "COMMITMENT_REOPENED"]


def test_full_readiness_does_not_record_customer_acceptance(services, approved):
    assessment = services.verification.assess(approved.project.id)
    assert assessment.readiness_pct == 100.0
    assert assessment.completion_state is CompletionState.INTERNALLY_VERIFIED
    assert services.repo.get_project(approved.project.id).acceptance is CustomerAcceptance.NOT_RECORDED


def test_acceptance_cannot_be_recorded_for_an_unverified_delivery(services, pending):
    with pytest.raises(ValidationError, match="every criterion is verified"):
        services.approvals.record_acceptance(pending.project.id, True, "Dana", "Email from sponsor")
    assert services.repo.get_project(pending.project.id).acceptance is CustomerAcceptance.NOT_RECORDED


def test_acceptance_is_recorded_explicitly_with_history(services, approved):
    services.approvals.record_acceptance(approved.project.id, True, "Dana", "Email from sponsor")
    project = services.repo.get_project(approved.project.id)
    assert (project.acceptance, project.acceptance_by, project.acceptance_note) == (
        CustomerAcceptance.ACCEPTED, "Dana", "Email from sponsor")
    entry = services.repo.list_audit(approved.project.id)[-1]
    assert (entry.action, entry.previous_state, entry.new_state) == (
        "CUSTOMER_ACCEPTANCE_RECORDED", "NOT_RECORDED", "ACCEPTED")


def test_decline_requires_a_note_and_is_allowed_before_verification(services, pending):
    with pytest.raises(ValidationError, match="required"):
        services.approvals.record_acceptance(pending.project.id, False, "Dana", "")
    services.approvals.record_acceptance(pending.project.id, False, "Dana", "Customer wants the export fixed first")
    assert services.repo.get_project(pending.project.id).acceptance is CustomerAcceptance.DECLINED


@pytest.mark.parametrize("change", ["add_evidence", "revoke", "reject", "add_criterion", "reopen"])
def test_acceptance_is_cleared_when_the_delivery_changes(services, approved, change):
    services.approvals.record_acceptance(approved.project.id, True, "Dana", "Email from sponsor")
    if change == "add_evidence":
        services.evidence.add_file(approved.criterion.id, "late.pdf.png", OTHER_PNG, "Late addition", "alice")
    elif change == "revoke":
        services.approvals.revoke(approved.criterion.id, "Dana", "Mistake")
    elif change == "reject":
        services.approvals.reject(approved.criterion.id, "Dana", "Defect found")
    elif change == "add_criterion":
        services.projects.add_criterion(approved.commitment.id, "Guide delivered", [EvidenceType.DOCUMENT])
    else:
        services.approvals.reopen_commitment(approved.commitment.id, "Dana", "Regression")
    assert services.repo.get_project(approved.project.id).acceptance is CustomerAcceptance.NOT_RECORDED
    assert "CUSTOMER_ACCEPTANCE_CLEARED" in actions(services, approved)


def test_acceptance_can_be_cleared_manually_with_a_reason(services, approved):
    with pytest.raises(ValidationError, match="No customer acceptance"):
        services.approvals.clear_acceptance(approved.project.id, "Dana", "Nothing there")
    services.approvals.record_acceptance(approved.project.id, True, "Dana", "Email")
    services.approvals.clear_acceptance(approved.project.id, "Dana", "Recorded for the wrong project")
    assert services.repo.get_project(approved.project.id).acceptance is CustomerAcceptance.NOT_RECORDED


def test_criterion_without_human_review_verifies_on_valid_evidence(services, seeded):
    auto = services.projects.add_criterion(seeded.commitment.id, "Guide delivered", [EvidenceType.DOCUMENT],
                                           requires_human_review=False)
    services.evidence.add_file(auto.id, "guide.pdf", PDF, "User guide", "alice")
    assert services.verification.assess(seeded.project.id).finding_for(auto.id).status is CriterionStatus.VERIFIED
