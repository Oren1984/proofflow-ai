"""Projects, commitments and acceptance criteria."""

from __future__ import annotations

from datetime import date

from app.models import (
    Commitment,
    Criterion,
    EvidenceType,
    Project,
    ProjectStatus,
    ReportedStatus,
    ValidationError,
    new_id,
    to_iso,
    utcnow,
)
from app.observability import log_event
from app.security import clean_text
from app.services.approval_service import ApprovalService
from app.storage import EvidenceStore, Repository


def _clean_date(value: str | date | None, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return (value if isinstance(value, date) else date.fromisoformat(value.strip())).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be a date in YYYY-MM-DD format.") from None


class ProjectService:
    def __init__(self, repo: Repository, store: EvidenceStore, approvals: ApprovalService) -> None:
        self._repo = repo
        self._store = store
        self._approvals = approvals

    # Projects
    def create_project(
        self,
        name: str,
        customer: str,
        description: str = "",
        delivery_date: str | date | None = None,
        status: ProjectStatus = ProjectStatus.ACTIVE,
    ) -> Project:
        name = clean_text(name, "Project name", 120, required=True)
        if any(p.name.lower() == name.lower() for p in self._repo.list_projects()):
            raise ValidationError(f"A project named '{name}' already exists.")
        project = Project(
            id=new_id("PRJ"),
            name=name,
            customer=clean_text(customer, "Customer", 120, required=True),
            description=clean_text(description, "Description", 1000),
            delivery_date=_clean_date(delivery_date, "Delivery date"),
            status=ProjectStatus(status),
            created_at=to_iso(utcnow()),
        )
        self._repo.insert_project(project)
        log_event("project.created", project_id=project.id)
        return project

    def update_project(
        self, project_id: str, *, customer: str, description: str, delivery_date: str | date | None,
        status: ProjectStatus,
    ) -> Project:
        project = self._repo.get_project(project_id)
        project.customer = clean_text(customer, "Customer", 120, required=True)
        project.description = clean_text(description, "Description", 1000)
        project.delivery_date = _clean_date(delivery_date, "Delivery date")
        project.status = ProjectStatus(status)
        self._repo.update_project(project)
        return project

    def delete_project(self, project_id: str) -> None:
        """Local deletion: removes the project's records, history and stored evidence files."""
        self._repo.get_project(project_id)
        self._repo.delete_project(project_id)
        self._store.delete_project(project_id)
        log_event("project.deleted", project_id=project_id)

    # Commitments
    def add_commitment(
        self,
        project_id: str,
        title: str,
        description: str = "",
        expected_deliverable: str = "",
        reported_status: ReportedStatus = ReportedStatus.NOT_STARTED,
        due_date: str | date | None = None,
    ) -> Commitment:
        self._repo.get_project(project_id)
        commitment = Commitment(
            id=new_id("CMT"),
            project_id=project_id,
            title=clean_text(title, "Commitment title", 160, required=True),
            description=clean_text(description, "Description", 1000),
            expected_deliverable=clean_text(expected_deliverable, "Expected deliverable", 300),
            reported_status=ReportedStatus(reported_status),
            due_date=_clean_date(due_date, "Due date"),
            position=len(self._repo.list_commitments(project_id)),
        )
        self._repo.insert_commitment(commitment)
        log_event("commitment.created", project_id=project_id, commitment_id=commitment.id)
        return commitment

    def update_commitment(
        self, commitment_id: str, *, title: str, description: str, expected_deliverable: str,
        reported_status: ReportedStatus, due_date: str | date | None,
    ) -> Commitment:
        commitment = self._repo.get_commitment(commitment_id)
        commitment.title = clean_text(title, "Commitment title", 160, required=True)
        commitment.description = clean_text(description, "Description", 1000)
        commitment.expected_deliverable = clean_text(expected_deliverable, "Expected deliverable", 300)
        commitment.reported_status = ReportedStatus(reported_status)
        commitment.due_date = _clean_date(due_date, "Due date")
        self._repo.update_commitment(commitment)
        return commitment

    def delete_commitment(self, commitment_id: str) -> None:
        commitment = self._repo.get_commitment(commitment_id)
        for criterion in self._repo.list_criteria(commitment_id):
            self.delete_criterion(criterion.id)
        self._repo.delete_commitment(commitment_id)
        self._approvals.reset_acceptance(commitment.project_id, f"Commitment {commitment_id} was deleted.")

    # Acceptance criteria
    @staticmethod
    def _validate_policy(
        allowed_types: list[EvidenceType], min_evidence: int, requires_passing_test: bool, max_age_days: int | None
    ) -> list[EvidenceType]:
        types = sorted({EvidenceType(t) for t in allowed_types})
        if not any(t is not EvidenceType.LINK for t in types):
            raise ValidationError(
                "Select at least one verifiable evidence type. External links alone can never satisfy a criterion."
            )
        if not 1 <= int(min_evidence) <= 10:
            raise ValidationError("Minimum evidence count must be between 1 and 10.")
        if requires_passing_test and EvidenceType.TEST_RESULT not in types:
            raise ValidationError("A criterion that requires a passing test must accept structured test results.")
        if max_age_days is not None and not 1 <= int(max_age_days) <= 3650:
            raise ValidationError("Maximum evidence age must be between 1 and 3650 days.")
        return types

    def add_criterion(
        self,
        commitment_id: str,
        text: str,
        allowed_types: list[EvidenceType],
        *,
        min_evidence: int = 1,
        requires_passing_test: bool = False,
        requires_human_review: bool = True,
        max_age_days: int | None = None,
    ) -> Criterion:
        commitment = self._repo.get_commitment(commitment_id)
        criterion = Criterion(
            id=new_id("CRT"),
            commitment_id=commitment_id,
            text=clean_text(text, "Acceptance criterion", 300, required=True),
            allowed_types=self._validate_policy(allowed_types, min_evidence, requires_passing_test, max_age_days),
            min_evidence=int(min_evidence),
            requires_passing_test=bool(requires_passing_test),
            requires_human_review=bool(requires_human_review),
            max_age_days=int(max_age_days) if max_age_days is not None else None,
            position=len(self._repo.list_criteria(commitment_id)),
        )
        self._repo.insert_criterion(criterion)
        self._approvals.reset_acceptance(commitment.project_id, f"Criterion {criterion.id} was added.")
        log_event("criterion.created", project_id=commitment.project_id, criterion_id=criterion.id)
        return criterion

    def update_criterion(
        self,
        criterion_id: str,
        *,
        text: str,
        allowed_types: list[EvidenceType],
        min_evidence: int,
        requires_passing_test: bool,
        requires_human_review: bool,
        max_age_days: int | None,
    ) -> Criterion:
        """Edit a criterion. Any change to its text or policy invalidates earlier review decisions."""
        criterion = self._repo.get_criterion(criterion_id)
        before = (criterion.text, criterion.allowed_types, criterion.min_evidence, criterion.requires_passing_test,
                  criterion.requires_human_review, criterion.max_age_days)
        criterion.text = clean_text(text, "Acceptance criterion", 300, required=True)
        criterion.allowed_types = self._validate_policy(allowed_types, min_evidence, requires_passing_test, max_age_days)
        criterion.min_evidence = int(min_evidence)
        criterion.requires_passing_test = bool(requires_passing_test)
        criterion.requires_human_review = bool(requires_human_review)
        criterion.max_age_days = int(max_age_days) if max_age_days is not None else None
        after = (criterion.text, criterion.allowed_types, criterion.min_evidence, criterion.requires_passing_test,
                 criterion.requires_human_review, criterion.max_age_days)
        if after != before:
            self._repo.update_criterion(criterion)
            self._approvals.invalidate(criterion_id, "The criterion text or evidence policy was edited.")
        return self._repo.get_criterion(criterion_id)

    def delete_criterion(self, criterion_id: str) -> None:
        criterion = self._repo.get_criterion(criterion_id)
        project_id = self._repo.get_commitment(criterion.commitment_id).project_id
        for evidence in self._repo.list_evidence(criterion_id=criterion_id):
            self._store.delete(evidence.stored_name)
        self._repo.delete_criterion(criterion_id)
        self._approvals.reset_acceptance(project_id, f"Criterion {criterion_id} was deleted.")
