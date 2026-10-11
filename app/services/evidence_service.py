"""Registers evidence and keeps review state consistent when evidence changes."""

from __future__ import annotations

import logging

from app import security
from app.models import Evidence, EvidenceType, ValidationError, new_id, to_iso, utcnow
from app.observability import log_event
from app.services.approval_service import ApprovalService
from app.storage import EvidenceStore, Repository


class EvidenceService:
    def __init__(
        self, repo: Repository, store: EvidenceStore, approvals: ApprovalService, max_upload_bytes: int
    ) -> None:
        self._repo = repo
        self._store = store
        self._approvals = approvals
        self._max_bytes = max_upload_bytes

    def add_file(
        self, criterion_id: str, filename: str, content: bytes, description: str, submitted_by: str
    ) -> Evidence:
        """Validate and store an uploaded file as evidence for one criterion."""
        try:
            safe_name, extension, evidence_type = security.validate_upload(filename, content, self._max_bytes)
        except ValidationError as exc:
            log_event("evidence.upload_rejected", level=logging.WARNING, criterion_id=criterion_id, reason=str(exc))
            raise
        return self._register(
            criterion_id, evidence_type, description, submitted_by,
            sha256=security.sha256_hex(content), original_name=safe_name, extension=extension, content=content,
        )

    def add_link(self, criterion_id: str, url: str, description: str, submitted_by: str) -> Evidence:
        """Record an external link as an unverified reference. The URL is never fetched."""
        url = security.validate_url(url)
        return self._register(
            criterion_id, EvidenceType.LINK, description, submitted_by,
            sha256=security.sha256_hex(url.encode("utf-8")), url=url,
        )

    def _register(
        self,
        criterion_id: str,
        evidence_type: EvidenceType,
        description: str,
        submitted_by: str,
        *,
        sha256: str,
        original_name: str | None = None,
        extension: str = "",
        content: bytes | None = None,
        url: str | None = None,
    ) -> Evidence:
        description = security.clean_text(description, "Description", 500, required=True)
        submitted_by = security.clean_text(submitted_by, "Submitted-by label", 80, required=True)
        criterion = self._repo.get_criterion(criterion_id)
        commitment = self._repo.get_commitment(criterion.commitment_id)
        if any(e.sha256 == sha256 for e in self._repo.list_evidence(criterion_id=criterion_id)):
            raise ValidationError("This exact evidence is already linked to the criterion.")

        evidence = Evidence(
            id=new_id("EVD"),
            project_id=commitment.project_id,
            commitment_id=commitment.id,
            criterion_id=criterion.id,
            type=evidence_type,
            description=description,
            submitted_by=submitted_by,
            submitted_at=to_iso(utcnow()),
            sha256=sha256,
            original_name=original_name,
            url=url,
            size_bytes=len(content) if content is not None else 0,
        )
        if content is not None:
            evidence.stored_name = self._store.save(evidence.project_id, evidence.id, extension, content)
        try:
            self._repo.insert_evidence(evidence)
        except ValidationError:
            self._store.delete(evidence.stored_name)
            raise
        self._repo.add_audit(
            project_id=evidence.project_id, at=evidence.submitted_at, action="EVIDENCE_ADDED",
            entity_type="evidence", entity_id=evidence.id, new_state=str(evidence.type),
            actor=submitted_by, reason=f"Linked to criterion {criterion.id}",
        )
        self._approvals.invalidate(criterion.id, f"Evidence {evidence.id} was added.")
        log_event("evidence.registered", project_id=evidence.project_id, criterion_id=criterion.id,
                  evidence_id=evidence.id, evidence_type=str(evidence.type), size_bytes=evidence.size_bytes)
        return evidence

    def remove(self, evidence_id: str, actor: str, reason: str = "") -> None:
        actor = security.clean_text(actor, "User label", 80, required=True)
        evidence = self._repo.get_evidence(evidence_id)
        self._repo.delete_evidence(evidence_id)
        self._store.delete(evidence.stored_name)
        self._repo.add_audit(
            project_id=evidence.project_id, at=to_iso(utcnow()), action="EVIDENCE_REMOVED",
            entity_type="evidence", entity_id=evidence.id, previous_state=str(evidence.type),
            actor=actor, reason=security.clean_text(reason, "Reason", 500) or None,
        )
        self._approvals.invalidate(evidence.criterion_id, f"Evidence {evidence.id} was removed.")
        log_event("evidence.removed", project_id=evidence.project_id, evidence_id=evidence.id)

    def read(self, evidence: Evidence) -> bytes | None:
        return self._store.read(evidence.stored_name)
