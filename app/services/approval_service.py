"""Human review decisions and customer acceptance, with an append-only history.

Reviewer names are local, user-entered labels - not authenticated identities - and a
decision here is not a legally binding signature.
"""

from __future__ import annotations

from app.models import (
    CompletionState,
    Criterion,
    CriterionStatus,
    CustomerAcceptance,
    ReportedStatus,
    ReviewDecision,
    ValidationError,
    to_iso,
    utcnow,
)
from app.observability import log_event
from app.security import clean_text
from app.services.verification_service import VerificationService
from app.storage import Repository

SYSTEM_ACTOR = "system"
REVIEWABLE = (CriterionStatus.PENDING_HUMAN_REVIEW, CriterionStatus.VERIFIED)


class ApprovalService:
    def __init__(self, repo: Repository, verification: VerificationService) -> None:
        self._repo = repo
        self._verification = verification

    def _project_id(self, criterion: Criterion) -> str:
        return self._repo.get_commitment(criterion.commitment_id).project_id

    def _decide(
        self, criterion: Criterion, decision: ReviewDecision, action: str, actor: str, reason: str, previous: str
    ) -> None:
        is_set = decision is not ReviewDecision.NONE
        criterion.decision = decision
        criterion.decision_by = actor if is_set else None
        criterion.decision_at = to_iso(utcnow()) if is_set else None
        criterion.decision_reason = (reason or None) if is_set else None
        criterion.decision_fingerprint = (
            self._verification.assess_criterion(criterion).fingerprint if is_set else None
        )
        self._repo.update_criterion(criterion)
        new_state = str(self._verification.assess_criterion(criterion).status)
        project_id = self._project_id(criterion)
        self._repo.add_audit(
            project_id=project_id, at=to_iso(utcnow()), action=action, entity_type="criterion",
            entity_id=criterion.id, previous_state=previous, new_state=new_state, actor=actor, reason=reason or None,
        )
        log_event("review." + action.lower(), project_id=project_id, criterion_id=criterion.id,
                  previous_state=previous, new_state=new_state)

    def approve(self, criterion_id: str, reviewer: str, note: str = "") -> None:
        reviewer = clean_text(reviewer, "Reviewer label", 80, required=True)
        note = clean_text(note, "Note", 500)
        criterion = self._repo.get_criterion(criterion_id)
        status = self._verification.assess_criterion(criterion).status
        if status is not CriterionStatus.PENDING_HUMAN_REVIEW:
            raise ValidationError(
                f"Cannot approve: the criterion is {status}. Only criteria that passed automated "
                "checks and are pending human review can be approved."
            )
        self._decide(criterion, ReviewDecision.APPROVED, "APPROVED", reviewer, note, str(status))

    def reject(self, criterion_id: str, reviewer: str, reason: str) -> None:
        reviewer = clean_text(reviewer, "Reviewer label", 80, required=True)
        reason = clean_text(reason, "Rejection reason", 500, required=True)
        criterion = self._repo.get_criterion(criterion_id)
        status = self._verification.assess_criterion(criterion).status
        if status not in REVIEWABLE:
            raise ValidationError(
                f"Cannot reject: the criterion is {status}. Automated findings already block it; "
                "rejection applies to evidence that passed automated checks."
            )
        self._decide(criterion, ReviewDecision.REJECTED, "REJECTED", reviewer, reason, str(status))
        self.reset_acceptance(self._project_id(criterion), "A criterion was rejected.")

    def revoke(self, criterion_id: str, reviewer: str, reason: str) -> None:
        """Withdraw an earlier approval or rejection, returning the criterion to review."""
        reviewer = clean_text(reviewer, "Reviewer label", 80, required=True)
        reason = clean_text(reason, "Revocation reason", 500, required=True)
        criterion = self._repo.get_criterion(criterion_id)
        if criterion.decision is ReviewDecision.NONE:
            raise ValidationError("There is no review decision to revoke.")
        previous = str(self._verification.assess_criterion(criterion).status)
        self._decide(criterion, ReviewDecision.NONE, "REVOKED", reviewer, reason, previous)
        self.reset_acceptance(self._project_id(criterion), "A review decision was revoked.")

    def reopen_commitment(self, commitment_id: str, reviewer: str, reason: str) -> None:
        """Clear every review decision under a commitment and mark it as in progress again."""
        reviewer = clean_text(reviewer, "Reviewer label", 80, required=True)
        reason = clean_text(reason, "Reopen reason", 500, required=True)
        commitment = self._repo.get_commitment(commitment_id)
        for criterion in self._repo.list_criteria(commitment_id):
            if criterion.decision is not ReviewDecision.NONE:
                previous = str(self._verification.assess_criterion(criterion).status)
                self._decide(criterion, ReviewDecision.NONE, "REVOKED", reviewer, f"Commitment reopened: {reason}", previous)
        previous_status = commitment.reported_status
        commitment.reported_status = ReportedStatus.IN_PROGRESS
        self._repo.update_commitment(commitment)
        self._repo.add_audit(
            project_id=commitment.project_id, at=to_iso(utcnow()), action="COMMITMENT_REOPENED",
            entity_type="commitment", entity_id=commitment.id, previous_state=str(previous_status),
            new_state=str(ReportedStatus.IN_PROGRESS), actor=reviewer, reason=reason,
        )
        self.reset_acceptance(commitment.project_id, "A commitment was reopened.")
        log_event("review.commitment_reopened", project_id=commitment.project_id, commitment_id=commitment.id)

    def invalidate(self, criterion_id: str, cause: str) -> None:
        """System action: evidence or the criterion changed, so earlier decisions no longer apply."""
        criterion = self._repo.get_criterion(criterion_id)
        project_id = self._project_id(criterion)
        if criterion.decision is not ReviewDecision.NONE:
            previous = str(criterion.decision)
            criterion.decision = ReviewDecision.NONE
            criterion.decision_by = criterion.decision_at = None
            criterion.decision_reason = criterion.decision_fingerprint = None
            self._repo.update_criterion(criterion)
            self._repo.add_audit(
                project_id=project_id, at=to_iso(utcnow()), action="DECISION_INVALIDATED",
                entity_type="criterion", entity_id=criterion.id, previous_state=previous,
                new_state=str(self._verification.assess_criterion(criterion).status),
                actor=SYSTEM_ACTOR, reason=cause,
            )
            log_event("review.decision_invalidated", project_id=project_id, criterion_id=criterion.id)
        self.reset_acceptance(project_id, cause)

    def record_acceptance(self, project_id: str, accepted: bool, recorded_by: str, note: str = "") -> None:
        """Record the customer's response. Separate from, and never implied by, internal verification."""
        recorded_by = clean_text(recorded_by, "Recorded-by label", 80, required=True)
        note = clean_text(note, "Note", 500, required=not accepted)
        project = self._repo.get_project(project_id)
        if accepted and self._verification.assess(project_id).completion_state is not CompletionState.INTERNALLY_VERIFIED:
            raise ValidationError(
                "Customer acceptance can only be recorded for a delivery in which every criterion is verified."
            )
        previous = project.acceptance
        project.acceptance = CustomerAcceptance.ACCEPTED if accepted else CustomerAcceptance.DECLINED
        project.acceptance_by = recorded_by
        project.acceptance_at = to_iso(utcnow())
        project.acceptance_note = note or None
        self._repo.update_project(project)
        self._repo.add_audit(
            project_id=project_id, at=project.acceptance_at, action="CUSTOMER_ACCEPTANCE_RECORDED",
            entity_type="project", entity_id=project_id, previous_state=str(previous),
            new_state=str(project.acceptance), actor=recorded_by, reason=note or None,
        )
        log_event("acceptance.recorded", project_id=project_id, new_state=str(project.acceptance))

    def clear_acceptance(self, project_id: str, actor: str, reason: str) -> None:
        actor = clean_text(actor, "Recorded-by label", 80, required=True)
        reason = clean_text(reason, "Reason", 500, required=True)
        if self._repo.get_project(project_id).acceptance is CustomerAcceptance.NOT_RECORDED:
            raise ValidationError("No customer acceptance is recorded.")
        self.reset_acceptance(project_id, reason, actor)

    def reset_acceptance(self, project_id: str, cause: str, actor: str = SYSTEM_ACTOR) -> None:
        project = self._repo.get_project(project_id)
        if project.acceptance is CustomerAcceptance.NOT_RECORDED:
            return
        previous = project.acceptance
        project.acceptance = CustomerAcceptance.NOT_RECORDED
        project.acceptance_by = project.acceptance_at = project.acceptance_note = None
        self._repo.update_project(project)
        self._repo.add_audit(
            project_id=project_id, at=to_iso(utcnow()), action="CUSTOMER_ACCEPTANCE_CLEARED",
            entity_type="project", entity_id=project_id, previous_state=str(previous),
            new_state=str(CustomerAcceptance.NOT_RECORDED), actor=actor, reason=cause,
        )
        log_event("acceptance.cleared", project_id=project_id)
