"""SQLite persistence, evidence registration and file-handling safety."""

from __future__ import annotations

import sqlite3

import pytest

from app.config import Settings
from app.models import CriterionStatus, EvidenceType, EvidenceValidity, ReportedStatus, ValidationError
from app.security import sha256_hex
from app.services import build_services
from tests.factories import PDF, PNG, suite_json


def test_project_structure_survives_a_restart(services, seeded):
    services.projects.add_commitment(seeded.project.id, "Second", reported_status=ReportedStatus.REPORTED_COMPLETE,
                                     due_date="2026-12-01")
    reopened = build_services(Settings(data_dir=services.settings.data_dir, seed_demo=False))
    project = reopened.repo.get_project(seeded.project.id)
    commitments = reopened.repo.list_commitments(project.id)
    assert (project.name, project.customer) == ("Test Project", "Test Customer")
    assert [c.title for c in commitments] == ["Deliver dashboard", "Second"]
    assert commitments[1].due_date == "2026-12-01"
    criterion = reopened.repo.list_criteria(commitments[0].id)[0]
    assert criterion.allowed_types == [EvidenceType.SCREENSHOT] and criterion.requires_human_review


def test_duplicate_project_names_are_rejected(services, seeded):
    with pytest.raises(ValidationError, match="already exists"):
        services.projects.create_project("test project", "Someone")


@pytest.mark.parametrize("kwargs", [
    {"allowed_types": []},
    {"allowed_types": [EvidenceType.LINK]},
    {"allowed_types": [EvidenceType.SCREENSHOT], "min_evidence": 0},
    {"allowed_types": [EvidenceType.SCREENSHOT], "requires_passing_test": True},
    {"allowed_types": [EvidenceType.SCREENSHOT], "max_age_days": 0},
])
def test_unsatisfiable_or_invalid_evidence_policies_are_rejected(services, seeded, kwargs):
    types = kwargs.pop("allowed_types")
    with pytest.raises(ValidationError):
        services.projects.add_criterion(seeded.commitment.id, "Another criterion", types, **kwargs)


def test_invalid_dates_and_blank_required_fields_are_rejected(services, seeded):
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        services.projects.add_commitment(seeded.project.id, "X", due_date="next week")
    with pytest.raises(ValidationError, match="required"):
        services.projects.add_commitment(seeded.project.id, "   ")
    with pytest.raises(ValidationError, match="required"):
        services.projects.create_project("Name", "")


def test_evidence_is_registered_with_hash_association_and_timestamp(services, seeded):
    evidence = services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    stored = services.repo.get_evidence(evidence.id)
    assert stored.sha256 == sha256_hex(PNG)
    assert (stored.criterion_id, stored.commitment_id, stored.project_id) == (
        seeded.criterion.id, seeded.commitment.id, seeded.project.id)
    assert stored.type is EvidenceType.SCREENSHOT and stored.submitted_by == "alice" and stored.submitted_at
    assert services.evidence.read(stored) == PNG


def test_duplicate_evidence_for_a_criterion_is_rejected(services, seeded):
    services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    with pytest.raises(ValidationError, match="already linked"):
        services.evidence.add_file(seeded.criterion.id, "copy.png", PNG, "Same bytes", "alice")
    assert len(services.repo.list_evidence(criterion_id=seeded.criterion.id)) == 1


def test_evidence_requires_description_and_submitter(services, seeded):
    with pytest.raises(ValidationError, match="Description"):
        services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "", "alice")
    with pytest.raises(ValidationError, match="Submitted-by"):
        services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home", " ")
    assert services.repo.list_evidence(project_id=seeded.project.id) == []


def test_evidence_for_an_unknown_criterion_is_rejected(services, seeded):
    with pytest.raises(ValidationError, match="does not exist"):
        services.evidence.add_file("CRT-NOPE", "home.png", PNG, "Home", "alice")


@pytest.mark.parametrize("filename", ["../../../outside.png", "..\\..\\outside.png", "/etc/cron.d/outside.png"])
def test_path_traversal_filenames_cannot_escape_the_evidence_store(services, seeded, filename):
    evidence = services.evidence.add_file(seeded.criterion.id, filename, PNG, "Hostile name", "mallory")
    root = services.settings.evidence_dir.resolve()
    files = [p for p in services.settings.data_dir.parent.rglob("*") if p.is_file() and p.suffix == ".png"]
    assert len(files) == 1 and files[0].resolve().is_relative_to(root)
    assert evidence.original_name == "outside.png"
    assert evidence.stored_name == f"{seeded.project.id}/{evidence.id}.png"


def test_store_refuses_paths_outside_its_root(services):
    assert services.store.read("../proofflow.db") is None
    with pytest.raises(ValidationError):
        services.store.save("..", "escape", ".png", PNG)
    with pytest.raises(ValidationError):
        services.store.delete("../proofflow.db")
    assert services.settings.db_path.exists()


def test_unsupported_and_oversized_uploads_are_rejected_and_nothing_is_stored(services, seeded):
    with pytest.raises(ValidationError, match="not allowed"):
        services.evidence.add_file(seeded.criterion.id, "tool.exe", b"MZ", "Binary", "mallory")
    with pytest.raises(ValidationError, match="limit"):
        services.evidence.add_file(seeded.criterion.id, "big.png", PNG + b"0" * (1024 * 1024), "Big", "mallory")
    with pytest.raises(ValidationError, match="signature"):
        services.evidence.add_file(seeded.criterion.id, "fake.png", b"<script>alert(1)</script>", "Fake", "mallory")
    assert services.repo.list_evidence(project_id=seeded.project.id) == []
    assert not any(services.settings.evidence_dir.rglob("*.*"))


def test_links_are_recorded_but_never_count(services, seeded):
    services.projects.update_criterion(
        seeded.criterion.id, text=seeded.criterion.text, allowed_types=[EvidenceType.SCREENSHOT, EvidenceType.LINK],
        min_evidence=1, requires_passing_test=False, requires_human_review=True, max_age_days=None)
    link = services.evidence.add_link(seeded.criterion.id, "https://example.com/review", "Meeting", "alice")
    finding = services.verification.assess(seeded.project.id).finding_for(seeded.criterion.id)
    assert link.stored_name is None and link.url == "https://example.com/review"
    assert finding.status is CriterionStatus.INSUFFICIENT_EVIDENCE
    assert finding.checks[0].validity is EvidenceValidity.UNVERIFIED_REFERENCE
    with pytest.raises(ValidationError):
        services.evidence.add_link(seeded.criterion.id, "javascript:alert(1)", "Bad", "mallory")


def test_corrupted_and_missing_files_are_detected_on_verification(services, seeded):
    evidence = services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    path = services.settings.evidence_dir / evidence.stored_name
    assert services.verification.assess(seeded.project.id).findings[0].status is CriterionStatus.PENDING_HUMAN_REVIEW

    path.write_bytes(PNG + b" modified after submission")
    finding = services.verification.assess(seeded.project.id).findings[0]
    assert finding.status is CriterionStatus.INVALID_EVIDENCE and "SHA-256" in finding.explanation

    path.unlink()
    finding = services.verification.assess(seeded.project.id).findings[0]
    assert finding.status is CriterionStatus.INVALID_EVIDENCE and "missing" in finding.explanation


def test_malformed_test_result_upload_is_stored_but_flagged_invalid(services, seeded):
    criterion = services.projects.add_criterion(
        seeded.commitment.id, "Tests pass", [EvidenceType.TEST_RESULT], requires_passing_test=True)
    services.evidence.add_file(criterion.id, "results.json", b'{"suite": "regression", "passed": "all"}', "CI", "ci")
    finding = services.verification.assess(seeded.project.id).finding_for(criterion.id)
    assert finding.status is CriterionStatus.INVALID_EVIDENCE


def test_verification_run_persists_validation_results_and_metrics(services, seeded):
    criterion = services.projects.add_criterion(
        seeded.commitment.id, "Tests pass", [EvidenceType.TEST_RESULT], requires_passing_test=True)
    evidence = services.evidence.add_file(criterion.id, "results.json", suite_json(failed=2), "CI run", "ci")
    assessment = services.verification.run(seeded.project.id)
    stored = services.repo.get_evidence(evidence.id)
    run = services.repo.list_runs(seeded.project.id)[0]
    assert stored.validation_status == "VALID" and "FAILED" in stored.validation_message
    assert (run["total"], run["verified"], run["readiness_pct"]) == (2, 0, 0.0)
    assert run["duration_ms"] >= 0 and run["rules_version"] and run["operation_id"]
    assert assessment.counts["MISSING_EVIDENCE"] == 1 and assessment.counts["INSUFFICIENT_EVIDENCE"] == 1


def test_same_inputs_and_time_reproduce_the_same_assessment(services, seeded):
    services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    first = services.verification.assess(seeded.project.id)
    assert services.verification.assess(seeded.project.id, now=None).findings == first.findings


def test_audit_history_is_append_only(services, seeded):
    services.evidence.add_file(seeded.criterion.id, "home.png", PNG, "Home page", "alice")
    assert [e.action for e in services.repo.list_audit(seeded.project.id)] == ["EVIDENCE_ADDED"]
    connection = sqlite3.connect(services.settings.db_path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE audit_log SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM audit_log")
    connection.close()
    assert len(services.repo.list_audit(seeded.project.id)) == 1


def test_deleting_a_project_removes_its_records_files_and_history(services, seeded):
    services.evidence.add_file(seeded.criterion.id, "guide.png", PNG, "Home page", "alice")
    other = services.projects.create_project("Other", "Other customer")
    services.projects.delete_project(seeded.project.id)
    assert [p.id for p in services.repo.list_projects()] == [other.id]
    assert services.repo.list_audit(seeded.project.id) == []
    assert services.repo.list_evidence(project_id=seeded.project.id) == []
    assert not (services.settings.evidence_dir / seeded.project.id).exists()


def test_deleting_a_criterion_removes_its_evidence_files(services, seeded):
    evidence = services.evidence.add_file(seeded.criterion.id, "guide.pdf.png", PNG, "Home page", "alice")
    services.projects.delete_criterion(seeded.criterion.id)
    assert not (services.settings.evidence_dir / evidence.stored_name).exists()
    assert services.verification.assess(seeded.project.id).total == 0


def test_document_evidence_type_is_derived_from_the_file_not_the_user(services, seeded):
    with_docs = services.projects.add_criterion(seeded.commitment.id, "Guide delivered", [EvidenceType.DOCUMENT])
    evidence = services.evidence.add_file(with_docs.id, "guide.pdf", PDF, "User guide", "alice")
    assert evidence.type is EvidenceType.DOCUMENT
