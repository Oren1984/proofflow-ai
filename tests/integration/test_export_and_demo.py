"""Delivery package generation, manifest consistency and the synthetic Before/After scenario."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app import demo_data
from app.models import CompletionState, CriterionStatus, ValidationError
from app.security import sha256_hex
from tests.factories import PNG

XSS = "<script>alert('proofflow')</script>"


def unpack(package) -> tuple[dict[str, bytes], dict, str]:
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert archive.testzip() is None
        files = {name: archive.read(name) for name in archive.namelist()}
    return files, json.loads(files["manifest.json"]), files["report.html"].decode("utf-8")


def manifest_evidence(manifest: dict) -> list[dict]:
    return [e for c in manifest["commitments"] for crit in c["criteria"] for e in crit["evidence"]]


@pytest.fixture
def demo(services):
    return demo_data.seed_before(services)


def approve_all(services, project_id: str, reviewer: str = "Dana (QA lead)") -> None:
    for finding in services.verification.assess(project_id).findings:
        services.approvals.approve(finding.criterion_id, reviewer)


def test_before_state_detects_every_planted_gap(services, demo):
    assessment = services.verification.run(demo.id)
    by_text = {c.text: assessment.finding_for(c.id).status for c in demo_data._criteria_by_text(services, demo.id).values()}
    assert by_text == {
        demo_data.ACCESSIBLE: CriterionStatus.PENDING_HUMAN_REVIEW,   # screenshot exists, nobody verified it
        demo_data.KPIS: CriterionStatus.MISSING_EVIDENCE,
        demo_data.CSV_EXPORT: CriterionStatus.INSUFFICIENT_EVIDENCE,  # "tests passed" note is not a test result
        demo_data.TESTS_PASS: CriterionStatus.INSUFFICIENT_EVIDENCE,  # test result reports failures
        demo_data.USER_GUIDE: CriterionStatus.INVALID_EVIDENCE,       # draft Markdown where a PDF is required
        demo_data.RELEASE_NOTES: CriterionStatus.MISSING_EVIDENCE,
        demo_data.FINAL_REVIEW: CriterionStatus.INSUFFICIENT_EVIDENCE,  # only an unverified link
    }
    assert (assessment.verified, assessment.total, assessment.readiness_pct) == (0, 7, 0.0)
    assert assessment.completion_state is CompletionState.NOT_READY
    assert all(c.reported_status == "REPORTED_COMPLETE" for c in services.repo.list_commitments(demo.id))


def test_after_state_resolves_gaps_but_still_needs_humans(services, demo):
    steps = demo_data.apply_after(services, demo.id)
    assessment = services.verification.assess(demo.id)
    assert len(steps) == 7
    assert assessment.counts["PENDING_HUMAN_REVIEW"] == 7 and assessment.verified == 0
    assert assessment.completion_state is CompletionState.AWAITING_REVIEW
    assert demo_data.apply_after(services, demo.id) == []  # idempotent

    approve_all(services, demo.id)
    assessment = services.verification.assess(demo.id)
    assert (assessment.readiness_pct, assessment.completion_state) == (100.0, CompletionState.INTERNALLY_VERIFIED)
    assert services.repo.get_project(demo.id).acceptance == "NOT_RECORDED"


def test_reseeding_replaces_the_demo_project(services, demo):
    again = demo_data.seed_before(services)
    assert again.id != demo.id and len(services.repo.list_projects()) == 1


def test_incomplete_package_is_labelled_and_lists_outstanding_items(services, demo):
    package = services.export.build(demo.id, "Dana")
    files, manifest, report = unpack(package)
    assert not package.complete and package.filename.endswith("_INCOMPLETE.zip")
    assert manifest["package"]["complete"] is False
    assert manifest["package"]["headline"].startswith("INCOMPLETE")
    assert manifest["completion"] == {
        "state": "NOT_READY", "readiness_pct": 0.0, "criteria_verified": 0, "criteria_total": 7,
        "status_counts": {"MISSING_EVIDENCE": 2, "INVALID_EVIDENCE": 1, "INSUFFICIENT_EVIDENCE": 3,
                          "PENDING_HUMAN_REVIEW": 1, "VERIFIED": 0, "REJECTED": 0},
    }
    assert len(manifest["outstanding_items"]) == 7
    assert all(item["explanation"] and item["next_action"] for item in manifest["outstanding_items"])
    assert "INCOMPLETE" in report and "Reported complete - NOT verified" in report
    assert "All criteria verified" not in report
    assert not any(c["verified"] for c in manifest["commitments"])


def test_manifest_is_internally_consistent_with_the_archive(services, demo):
    demo_data.apply_after(services, demo.id)
    approve_all(services, demo.id)
    package = services.export.build(demo.id, "Dana")
    files, manifest, report = unpack(package)

    entries = manifest_evidence(manifest)
    packaged = {e["package_path"]: e for e in entries if e["package_path"]}
    assert set(files) == {"report.html", "manifest.json", *packaged}
    for path, entry in packaged.items():
        assert sha256_hex(files[path]) == entry["sha256"]
        assert len(files[path]) == entry["size_bytes"]
        assert entry["sha256"] in report
    links = [e for e in entries if e["type"] == "LINK"]
    assert links and all(e["package_path"] is None and e["validity"] == "UNVERIFIED_REFERENCE" for e in links)

    assert {e["id"] for e in entries} == {e.id for e in services.repo.list_evidence(project_id=demo.id)}
    statuses = [crit["status"] for c in manifest["commitments"] for crit in c["criteria"]]
    assert statuses.count("VERIFIED") == manifest["completion"]["criteria_verified"] == 7
    assert manifest["outstanding_items"] == []
    assert sha256_hex(files["manifest.json"]) in report
    approvals = [h for h in manifest["review_history"] if h["action"] == "APPROVED"]
    assert len(approvals) == 7 and all(h["actor"] == "Dana (QA lead)" for h in approvals)


def test_verified_package_keeps_customer_acceptance_separate(services, demo):
    demo_data.apply_after(services, demo.id)
    approve_all(services, demo.id)
    package = services.export.build(demo.id, "Dana")
    _, manifest, report = unpack(package)
    assert package.complete and "INCOMPLETE" not in package.filename
    assert manifest["package"]["headline"] == "Internally verified - customer acceptance NOT recorded"
    assert manifest["customer_acceptance"]["state"] == "NOT_RECORDED"
    assert "Customer acceptance is outstanding" in report

    services.approvals.record_acceptance(demo.id, True, "Dana", "Signed handover email")
    _, manifest, report = unpack(services.export.build(demo.id, "Dana"))
    assert manifest["package"]["headline"] == "Internally verified - customer acceptance recorded"
    assert manifest["customer_acceptance"]["recorded_by"] == "Dana"
    assert "Customer acceptance is outstanding" not in report


def test_corrupted_evidence_is_not_packaged_and_blocks_completion(services, demo):
    demo_data.apply_after(services, demo.id)
    approve_all(services, demo.id)
    victim = services.repo.list_evidence(project_id=demo.id)[0]
    (services.settings.evidence_dir / victim.stored_name).write_bytes(PNG + b"swapped after approval")
    package = services.export.build(demo.id, "Dana")
    files, manifest, _ = unpack(package)
    entry = next(e for e in manifest_evidence(manifest) if e["id"] == victim.id)
    assert not package.complete
    assert entry["validity"] == "INVALID" and entry["package_path"] is None
    assert not any(victim.id in name for name in files)


def test_report_and_archive_are_safe_against_hostile_text_and_names(services):
    project = services.projects.create_project(f"Project {XSS}", f"Customer {XSS}", description=XSS)
    commitment = services.projects.add_commitment(project.id, f"Commitment {XSS}", XSS, XSS)
    criterion = services.projects.add_criterion(commitment.id, f"Criterion {XSS}", ["SCREENSHOT", "LINK"])
    services.evidence.add_file(criterion.id, "../../../evil<script>.png", PNG, XSS, XSS)
    services.evidence.add_link(criterion.id, "https://example.com/?q=<script>alert(1)</script>", XSS, XSS)
    services.approvals.reject(criterion.id, XSS, XSS)

    files, manifest, report = unpack(services.export.build(project.id, XSS))
    assert "<script" not in report and "&lt;script&gt;" in report
    assert manifest["project"]["name"] == f"Project {XSS}"  # JSON keeps the literal text as data
    for name in files:
        assert not name.startswith(("/", "\\")) and ".." not in name and "\\" not in name and "<" not in name
    evidence_files = [name for name in files if name.startswith("evidence/")]
    assert len(evidence_files) == 1 and evidence_files[0].startswith("evidence/EVD-")


def test_export_is_recorded_and_failures_are_counted(services, demo, monkeypatch):
    services.export.build(demo.id, "Dana")
    assert services.repo.list_audit(demo.id)[-1].action == "EXPORT_GENERATED"
    assert services.repo.list_audit(demo.id)[-1].new_state == "INCOMPLETE"

    def explode(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(zipfile.ZipFile, "writestr", explode)
    with pytest.raises(OSError):
        services.export.build(demo.id, "Dana")
    assert services.repo.list_audit(demo.id)[-1].action == "EXPORT_FAILED"


def test_export_requires_a_label_and_an_existing_project(services, demo):
    with pytest.raises(ValidationError, match="Generated-by"):
        services.export.build(demo.id, "")
    with pytest.raises(ValidationError, match="does not exist"):
        services.export.build("PRJ-MISSING", "Dana")


def test_empty_project_exports_as_incomplete(services):
    project = services.projects.create_project("Empty", "Nobody")
    package = services.export.build(project.id, "Dana")
    _, manifest, report = unpack(package)
    assert not package.complete and manifest["completion"]["state"] == "NO_CRITERIA"
    assert manifest["completion"]["readiness_pct"] == 0.0 and "INCOMPLETE" in report
