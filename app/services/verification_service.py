"""Runs the deterministic rules over a project's recorded inputs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime

from app import rules
from app.models import Assessment, Criterion, CriterionStatus, EvidenceValidity, Finding, utcnow
from app.observability import log_event, new_operation_id
from app.storage import EvidenceStore, Repository


class VerificationService:
    def __init__(
        self,
        repo: Repository,
        store: EvidenceStore,
        max_upload_bytes: int,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._store = store
        self._max_bytes = max_upload_bytes
        self._clock = clock

    def assess_criterion(self, criterion: Criterion, now: datetime | None = None) -> Finding:
        now = now or self._clock()
        evidence = self._repo.list_evidence(criterion_id=criterion.id)
        checks = [
            rules.check_evidence(item, criterion, self._store.read(item.stored_name), now, self._max_bytes)
            for item in evidence
        ]
        return rules.evaluate_criterion(criterion, evidence, checks)

    def assess(self, project_id: str, now: datetime | None = None) -> Assessment:
        """Compute the current assessment from recorded inputs. Read-only."""
        now = now or self._clock()
        self._repo.get_project(project_id)  # unknown projects are an error, not an empty assessment
        findings = [
            self.assess_criterion(criterion, now)
            for commitment in self._repo.list_commitments(project_id)
            for criterion in self._repo.list_criteria(commitment.id)
        ]
        return rules.summarize(project_id, findings, now)

    def run(self, project_id: str) -> Assessment:
        """Execute a recorded verification run: assess, persist validation results and metrics."""
        operation_id = new_operation_id()
        started = time.perf_counter()
        assessment = self.assess(project_id)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        checks = [check for finding in assessment.findings for check in finding.checks]
        self._repo.set_evidence_validation([(str(c.validity), c.message, c.evidence_id) for c in checks])
        self._repo.add_run(
            project_id=project_id,
            operation_id=operation_id,
            run_at=assessment.assessed_at,
            duration_ms=duration_ms,
            rules_version=rules.RULES_VERSION,
            total=assessment.total,
            verified=assessment.verified,
            readiness_pct=assessment.readiness_pct,
            counts=json.dumps(assessment.counts),
        )
        validation_failures = sum(1 for c in checks if c.validity is EvidenceValidity.INVALID)
        log_event(
            "verification.completed",
            project_id=project_id,
            operation_id=operation_id,
            duration_ms=duration_ms,
            criteria_total=assessment.total,
            criteria_verified=assessment.verified,
            missing_evidence=assessment.counts[CriterionStatus.MISSING_EVIDENCE],
            validation_failures=validation_failures,
            readiness_pct=assessment.readiness_pct,
            completion_state=str(assessment.completion_state),
        )
        for check in checks:
            if check.validity is EvidenceValidity.INVALID:
                log_event("evidence.validation_failed", project_id=project_id, operation_id=operation_id,
                          evidence_id=check.evidence_id, problems=len(check.messages))
        return assessment
