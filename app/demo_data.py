"""Synthetic demonstration scenario: "Customer Analytics Dashboard Delivery".

`seed_before` creates a delivery that is reported complete but poorly evidenced.
`apply_after` supplies the valid evidence. Both go through the normal services, so the
demo exercises exactly the same validation as manual use. Human approval and customer
acceptance are deliberately left to the person running the demo.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from app.models import (
    Criterion,
    CriterionStatus,
    EvidenceType,
    Project,
    ReportedStatus,
    ValidationError,
    to_iso,
    utcnow,
)
from app.observability import log_event
from app.services import Services

DEMO_PROJECT_NAME = "Customer Analytics Dashboard"
DEMO_CUSTOMER = "Northwind Retail (synthetic)"
DEMO_ACTOR = "delivery-team (demo)"
DEMO_DIR = Path(__file__).resolve().parent.parent / "demo" / "synthetic_data"

ACCESSIBLE = "Dashboard is accessible at the agreed URL"
KPIS = "Required KPIs (revenue, active customers, churn) are displayed"
CSV_EXPORT = "CSV export produces the expected fields"
TESTS_PASS = "Required automated tests pass"
USER_GUIDE = "User guide is delivered as a PDF"
RELEASE_NOTES = "Release notes are delivered"
FINAL_REVIEW = "Final stakeholder review is recorded"

S, D, T, R, L = (EvidenceType.SCREENSHOT, EvidenceType.DOCUMENT, EvidenceType.TEXT,
                 EvidenceType.TEST_RESULT, EvidenceType.LINK)

# (commitment title, description, expected deliverable, [(criterion text, accepted types, policy)])
SCENARIO = [
    ("Dashboard Delivery", "Deliver the customer reporting dashboard.", "Deployed analytics dashboard",
     [(ACCESSIBLE, [S], {}), (KPIS, [S], {})]),
    ("Data Export", "Users can export the KPI table.", "CSV export feature",
     [(CSV_EXPORT, [R, T], {"requires_passing_test": True})]),
    ("Quality Validation", "The release passes the agreed automated test suite.", "Passing regression run",
     [(TESTS_PASS, [R], {"requires_passing_test": True, "max_age_days": 30})]),
    ("Documentation", "Hand over end-user documentation.", "User guide and release notes",
     [(USER_GUIDE, [D], {}), (RELEASE_NOTES, [T, D], {})]),
    ("Customer Handover", "Walk the customer through the delivery.", "Recorded final review",
     [(FINAL_REVIEW, [D, T, L], {})]),
]


def _test_result(suite: str, total: int, failed: int, age: timedelta) -> bytes:
    record = {"suite": suite, "total": total, "passed": total - failed, "failed": failed, "skipped": 0,
              "executed_at": to_iso(utcnow() - age)}
    return json.dumps(record, indent=2).encode("utf-8")


def _criteria_by_text(services: Services, project_id: str) -> dict[str, Criterion]:
    return {
        criterion.text: criterion
        for commitment in services.repo.list_commitments(project_id)
        for criterion in services.repo.list_criteria(commitment.id)
    }


def find_demo_project(services: Services) -> Project | None:
    return next((p for p in services.repo.list_projects() if p.name == DEMO_PROJECT_NAME), None)


def seed_before(services: Services) -> Project:
    """(Re)create the demo project in its 'Before' state: reported complete, not verifiable."""
    existing = find_demo_project(services)
    if existing:
        services.projects.delete_project(existing.id)
    project = services.projects.create_project(
        DEMO_PROJECT_NAME, DEMO_CUSTOMER,
        "Synthetic demo: a small team delivers a reporting dashboard and reports all work as complete.",
        delivery_date=(utcnow() + timedelta(days=7)).date(),
    )
    for title, description, deliverable, criteria in SCENARIO:
        commitment = services.projects.add_commitment(
            project.id, title, description, deliverable, ReportedStatus.REPORTED_COMPLETE
        )
        for text, types, policy in criteria:
            services.projects.add_criterion(commitment.id, text, types, **policy)

    by_text = _criteria_by_text(services, project.id)
    add_file, add_link = services.evidence.add_file, services.evidence.add_link
    add_file(by_text[ACCESSIBLE].id, "dashboard_home.png", (DEMO_DIR / "dashboard_home.png").read_bytes(),
             "Screenshot of the dashboard home page on staging", DEMO_ACTOR)
    add_file(by_text[CSV_EXPORT].id, "export_tests_passed.txt", (DEMO_DIR / "export_tests_passed.txt").read_bytes(),
             "Developer note saying the export tests passed", DEMO_ACTOR)
    add_file(by_text[TESTS_PASS].id, "regression_results.json",
             _test_result("regression", 42, 3, timedelta(days=2)), "Regression suite run from CI", DEMO_ACTOR)
    add_file(by_text[USER_GUIDE].id, "user_guide_draft.md", (DEMO_DIR / "user_guide_draft.md").read_bytes(),
             "Draft of the user guide", DEMO_ACTOR)
    add_link(by_text[FINAL_REVIEW].id, "https://example.com/meetings/final-review",
             "Calendar entry for the final review meeting", DEMO_ACTOR)
    log_event("demo.seeded", project_id=project.id)
    return project


def apply_after(services: Services, project_id: str) -> list[str]:
    """Supply the missing/corrected evidence. Idempotent; returns a description of each step taken."""
    by_text = _criteria_by_text(services, project_id)
    steps: list[str] = []

    def status(text: str) -> CriterionStatus:
        return services.verification.assess_criterion(services.repo.get_criterion(by_text[text].id)).status

    def attach(text: str, filename: str, content: bytes, description: str) -> None:
        try:
            services.evidence.add_file(by_text[text].id, filename, content, description, DEMO_ACTOR)
            steps.append(f"Attached {filename} to '{text}'")
        except ValidationError:
            pass  # already attached

    attach(KPIS, "dashboard_kpis.png", (DEMO_DIR / "dashboard_kpis.png").read_bytes(),
           "Screenshot of the KPI page showing revenue, active customers and churn")
    if status(CSV_EXPORT) is CriterionStatus.INSUFFICIENT_EVIDENCE:
        attach(CSV_EXPORT, "export_results.json", _test_result("csv-export", 12, 0, timedelta(hours=1)),
               "CSV export test suite run from CI")
    if status(TESTS_PASS) is CriterionStatus.INSUFFICIENT_EVIDENCE:
        attach(TESTS_PASS, "regression_results_rerun.json", _test_result("regression", 42, 0, timedelta(minutes=30)),
               "Regression suite re-run after fixes")
    for evidence in services.repo.list_evidence(criterion_id=by_text[USER_GUIDE].id):
        if evidence.type is EvidenceType.TEXT:
            services.evidence.remove(evidence.id, DEMO_ACTOR, "Replaced by the final PDF user guide")
            steps.append("Removed the draft Markdown user guide")
    attach(USER_GUIDE, "user_guide.pdf", (DEMO_DIR / "user_guide.pdf").read_bytes(), "Final user guide v1.0")
    attach(RELEASE_NOTES, "release_notes.md", (DEMO_DIR / "release_notes.md").read_bytes(), "Release notes 1.0")
    attach(FINAL_REVIEW, "stakeholder_review_minutes.pdf",
           (DEMO_DIR / "stakeholder_review_minutes.pdf").read_bytes(), "Minutes of the final stakeholder review")
    log_event("demo.after_applied", project_id=project_id, steps=len(steps))
    return steps
