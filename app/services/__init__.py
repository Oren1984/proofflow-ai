"""Service wiring."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.services.approval_service import ApprovalService
from app.services.evidence_service import EvidenceService
from app.services.export_service import ExportService
from app.services.project_service import ProjectService
from app.services.verification_service import VerificationService
from app.storage import EvidenceStore, Repository


@dataclass(frozen=True)
class Services:
    settings: Settings
    repo: Repository
    store: EvidenceStore
    projects: ProjectService
    evidence: EvidenceService
    verification: VerificationService
    approvals: ApprovalService
    export: ExportService


def build_services(settings: Settings) -> Services:
    repo = Repository(settings.db_path)
    store = EvidenceStore(settings.evidence_dir)
    verification = VerificationService(repo, store, settings.max_upload_bytes)
    approvals = ApprovalService(repo, verification)
    return Services(
        settings=settings,
        repo=repo,
        store=store,
        projects=ProjectService(repo, store, approvals),
        evidence=EvidenceService(repo, store, approvals, settings.max_upload_bytes),
        verification=verification,
        approvals=approvals,
        export=ExportService(repo, store, verification),
    )
