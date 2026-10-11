"""Builds the downloadable delivery package: HTML report, JSON manifest and evidence files in a ZIP."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import asdict, dataclass
from html import escape
from pathlib import PurePosixPath

from app import __version__, rules
from app.models import (
    Assessment,
    CompletionState,
    CriterionStatus,
    CustomerAcceptance,
    ReportedStatus,
    ReviewDecision,
    to_iso,
    utcnow,
)
from app.observability import log_event, new_operation_id
from app.security import ALLOWED_EXTENSIONS, clean_text, sha256_hex
from app.services.verification_service import VerificationService
from app.storage import EvidenceStore, Repository

DISCLAIMERS = [
    "Verification statuses come from deterministic rules plus recorded human review decisions.",
    "SHA-256 hashes show that files are unchanged since submission; they do not prove that the content is true.",
    "External links are unverified references; their content was not fetched or checked.",
    "Reviewer names are user-entered labels, not authenticated identities. Approvals are not electronic signatures.",
    "Internal verification is not customer acceptance. Acceptance is shown only when explicitly recorded.",
]

STATUS_CLASS = {
    CriterionStatus.VERIFIED: "ok",
    CriterionStatus.PENDING_HUMAN_REVIEW: "warn",
}


@dataclass(frozen=True)
class DeliveryPackage:
    filename: str
    content: bytes
    manifest: dict
    complete: bool


def headline(state: CompletionState, acceptance: CustomerAcceptance, verified: int, total: int) -> str:
    """One-line delivery state that never overstates completion or acceptance."""
    if state is not CompletionState.INTERNALLY_VERIFIED:
        return f"INCOMPLETE - not verified ({verified} of {total} acceptance criteria verified)"
    if acceptance is CustomerAcceptance.ACCEPTED:
        return "Internally verified - customer acceptance recorded"
    if acceptance is CustomerAcceptance.DECLINED:
        return "Internally verified - customer DECLINED acceptance"
    return "Internally verified - customer acceptance NOT recorded"


class ExportService:
    def __init__(self, repo: Repository, store: EvidenceStore, verification: VerificationService) -> None:
        self._repo = repo
        self._store = store
        self._verification = verification

    def build(self, project_id: str, generated_by: str) -> DeliveryPackage:
        generated_by = clean_text(generated_by, "Generated-by label", 80, required=True)
        self._repo.get_project(project_id)
        operation_id = new_operation_id()
        try:
            package = self._build(project_id, generated_by)
        except Exception:
            self._record(project_id, "EXPORT_FAILED", generated_by, None)
            log_event("export.failed", level=logging.ERROR, exc_info=True, project_id=project_id,
                      operation_id=operation_id)
            raise
        self._record(project_id, "EXPORT_GENERATED", generated_by, "COMPLETE" if package.complete else "INCOMPLETE")
        log_event("export.generated", project_id=project_id, operation_id=operation_id,
                  complete=package.complete, size_bytes=len(package.content))
        return package

    def _record(self, project_id: str, action: str, actor: str, state: str | None) -> None:
        self._repo.add_audit(project_id=project_id, at=to_iso(utcnow()), action=action, entity_type="project",
                             entity_id=project_id, new_state=state, actor=actor)

    def _build(self, project_id: str, generated_by: str) -> DeliveryPackage:
        assessment = self._verification.run(project_id)
        files: dict[str, bytes] = {}
        manifest = self._manifest(assessment, generated_by, files)
        manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        report = render_report(manifest, sha256_hex(manifest_bytes))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.html", report)
            archive.writestr("manifest.json", manifest_bytes)
            for archive_path, content in files.items():
                archive.writestr(archive_path, content)

        complete = manifest["package"]["complete"]
        stamp = manifest["package"]["generated_at"].replace("-", "").replace(":", "").replace("+0000", "Z")
        suffix = "" if complete else "_INCOMPLETE"
        return DeliveryPackage(f"proofflow_{project_id}_{stamp}{suffix}.zip", buffer.getvalue(), manifest, complete)

    def _manifest(self, assessment: Assessment, generated_by: str, files: dict[str, bytes]) -> dict:
        project = self._repo.get_project(assessment.project_id)
        complete = assessment.completion_state is CompletionState.INTERNALLY_VERIFIED
        commitments, outstanding = [], []

        for commitment in self._repo.list_commitments(project.id):
            criteria = []
            for criterion in self._repo.list_criteria(commitment.id):
                finding = assessment.finding_for(criterion.id)
                checks = {check.evidence_id: check for check in finding.checks}
                evidence_entries = []
                for evidence in self._repo.list_evidence(criterion_id=criterion.id):
                    check = checks[evidence.id]
                    entry = {
                        "id": evidence.id,
                        "type": str(evidence.type),
                        "description": evidence.description,
                        "original_name": evidence.original_name,
                        "url": evidence.url,
                        "sha256": evidence.sha256,
                        "size_bytes": evidence.size_bytes,
                        "submitted_by": evidence.submitted_by,
                        "submitted_at": evidence.submitted_at,
                        "validity": str(check.validity),
                        "validation_message": check.message,
                        "package_path": None,
                    }
                    content = self._store.read(evidence.stored_name)
                    extension = PurePosixPath(evidence.stored_name or "").suffix.lower()
                    # Archive names are built only from generated IDs and allow-listed extensions.
                    if content is not None and extension in ALLOWED_EXTENSIONS and sha256_hex(content) == evidence.sha256:
                        entry["package_path"] = f"evidence/{evidence.id}{extension}"
                        files[entry["package_path"]] = content
                    evidence_entries.append(entry)

                decision = ReviewDecision.NONE if finding.decision_stale else criterion.decision
                has_decision = decision is not ReviewDecision.NONE
                criteria.append({
                    "id": criterion.id,
                    "text": criterion.text,
                    "policy": {
                        "accepted_evidence_types": [str(t) for t in criterion.allowed_types],
                        "min_evidence": criterion.min_evidence,
                        "requires_passing_test": criterion.requires_passing_test,
                        "requires_human_review": criterion.requires_human_review,
                        "max_age_days": criterion.max_age_days,
                    },
                    "status": str(finding.status),
                    "explanation": finding.explanation,
                    "missing": list(finding.missing),
                    "next_action": finding.next_action,
                    "review": {
                        "decision": str(decision),
                        "by": criterion.decision_by if has_decision else None,
                        "at": criterion.decision_at if has_decision else None,
                        "reason": criterion.decision_reason if has_decision else None,
                    },
                    "evidence": evidence_entries,
                })
                if finding.status is not CriterionStatus.VERIFIED:
                    outstanding.append({
                        "commitment_id": commitment.id,
                        "commitment": commitment.title,
                        "criterion_id": criterion.id,
                        "criterion": criterion.text,
                        "status": str(finding.status),
                        "explanation": finding.explanation,
                        "missing": list(finding.missing),
                        "next_action": finding.next_action,
                    })
            verified = sum(1 for c in criteria if c["status"] == CriterionStatus.VERIFIED)
            commitments.append({
                "id": commitment.id,
                "title": commitment.title,
                "description": commitment.description,
                "expected_deliverable": commitment.expected_deliverable,
                "reported_status": str(commitment.reported_status),
                "due_date": commitment.due_date,
                "criteria_verified": verified,
                "criteria_total": len(criteria),
                "verified": bool(criteria) and verified == len(criteria),
                "criteria": criteria,
            })

        return {
            "package": {
                "format_version": "1.0",
                "generator": f"ProofFlow AI {__version__}",
                "rules_version": rules.RULES_VERSION,
                "generated_at": assessment.assessed_at,
                "generated_by": generated_by,
                "complete": complete,
                "headline": headline(assessment.completion_state, project.acceptance, assessment.verified, assessment.total),
            },
            "project": {
                "id": project.id,
                "name": project.name,
                "customer": project.customer,
                "description": project.description,
                "delivery_date": project.delivery_date,
                "status": str(project.status),
            },
            "completion": {
                "state": str(assessment.completion_state),
                "readiness_pct": assessment.readiness_pct,
                "criteria_verified": assessment.verified,
                "criteria_total": assessment.total,
                "status_counts": assessment.counts,
            },
            "customer_acceptance": {
                "state": str(project.acceptance),
                "recorded_by": project.acceptance_by,
                "recorded_at": project.acceptance_at,
                "note": project.acceptance_note,
            },
            "commitments": commitments,
            "outstanding_items": outstanding,
            "review_history": [asdict(entry) for entry in self._repo.list_audit(project.id)],
            "disclaimers": DISCLAIMERS,
        }


REPORT_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f2933;margin:0;background:#f4f6f8;line-height:1.45}
main{max-width:980px;margin:0 auto;padding:32px 24px;background:#fff}
h1{margin:0 0 4px;font-size:26px}h2{margin:32px 0 10px;font-size:19px;border-bottom:2px solid #e3e8ee;padding-bottom:6px}
h3{margin:22px 0 6px;font-size:16px}.muted{color:#5b6875;font-size:13px}
.banner{padding:14px 18px;border-radius:8px;font-weight:600;margin:18px 0;border:1px solid}
.banner.ok{background:#e8f6ee;border-color:#2f855a;color:#1c5c3c}.banner.warn{background:#fff6e0;border-color:#b7791f;color:#7a4f0c}
.banner.bad{background:#fdecec;border-color:#c53030;color:#8f1f1f}
table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:13.5px}
th,td{border:1px solid #dde3ea;padding:7px 9px;text-align:left;vertical-align:top;word-break:break-word}
th{background:#f1f4f7}.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;white-space:nowrap}
.tag.ok{background:#d9f2e3;color:#1c5c3c}.tag.warn{background:#fdecc4;color:#7a4f0c}.tag.bad{background:#fbd5d5;color:#8f1f1f}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px}ul{margin:6px 0 10px 20px;padding:0}
.criterion{border:1px solid #dde3ea;border-radius:8px;padding:12px 14px;margin:10px 0}
footer{margin-top:36px;padding-top:14px;border-top:1px solid #dde3ea}
"""


def _tag(status: str) -> str:
    css = STATUS_CLASS.get(CriterionStatus(status), "bad")
    return f'<span class="tag {css}">{escape(status)}</span>'


def _cell(value: object) -> str:
    return escape(str(value)) if value not in (None, "") else "&mdash;"


def render_report(manifest: dict, manifest_sha256: str) -> str:
    """Render the manifest as a standalone HTML report. Every dynamic value is HTML-escaped."""
    package, project = manifest["package"], manifest["project"]
    completion, acceptance = manifest["completion"], manifest["customer_acceptance"]
    banner_class = "bad" if not package["complete"] else (
        "ok" if acceptance["state"] == CustomerAcceptance.ACCEPTED else "warn"
    )
    out: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>Delivery report - {escape(project['name'])}</title><style>{REPORT_CSS}</style></head><body><main>",
        f"<h1>Delivery Completion Report</h1><div class='muted'>{escape(project['name'])} &middot; "
        f"prepared for {escape(project['customer'])}</div>",
        f"<div class='banner {banner_class}'>{escape(package['headline'])}</div>",
        "<h2>Summary</h2><table>",
        f"<tr><th>Project</th><td>{_cell(project['name'])} <span class='muted'>({escape(project['id'])})</span></td></tr>",
        f"<tr><th>Customer / stakeholder</th><td>{_cell(project['customer'])}</td></tr>",
        f"<tr><th>Description</th><td>{_cell(project['description'])}</td></tr>",
        f"<tr><th>Planned delivery date</th><td>{_cell(project['delivery_date'])}</td></tr>",
        f"<tr><th>Internal completion state</th><td><strong>{escape(completion['state'])}</strong> &middot; "
        f"{completion['criteria_verified']} of {completion['criteria_total']} criteria verified "
        f"({completion['readiness_pct']}% readiness)</td></tr>",
        f"<tr><th>Customer acceptance</th><td><strong>{escape(acceptance['state'])}</strong>",
    ]
    if acceptance["state"] != CustomerAcceptance.NOT_RECORDED:
        out.append(
            f" &middot; recorded by {_cell(acceptance['recorded_by'])} on {_cell(acceptance['recorded_at'])}"
            f"<br><span class='muted'>Note: {_cell(acceptance['note'])}</span>"
        )
    else:
        out.append(" &middot; tracked separately; not implied by internal verification")
    out.append("</td></tr></table>")

    out.append("<h2>Exceptions and outstanding items</h2>")
    if manifest["outstanding_items"]:
        out.append("<table><tr><th>Commitment</th><th>Criterion</th><th>Status</th><th>Finding</th><th>Next action</th></tr>")
        for item in manifest["outstanding_items"]:
            gaps = "".join(f"<li>{escape(gap)}</li>" for gap in item["missing"])
            out.append(
                f"<tr><td>{_cell(item['commitment'])}</td><td>{_cell(item['criterion'])}</td>"
                f"<td>{_tag(item['status'])}</td><td>{_cell(item['explanation'])}"
                f"{'<ul>' + gaps + '</ul>' if gaps else ''}</td><td>{_cell(item['next_action'])}</td></tr>"
            )
        out.append("</table>")
    else:
        out.append("<p>No outstanding verification items.</p>")
    if acceptance["state"] != CustomerAcceptance.ACCEPTED:
        out.append("<p><strong>Customer acceptance is outstanding.</strong> It must be recorded explicitly.</p>")

    out.append("<h2>Commitments and acceptance criteria</h2>")
    for commitment in manifest["commitments"]:
        if commitment["verified"]:
            state = "<span class='tag ok'>All criteria verified</span>"
        elif commitment["reported_status"] == ReportedStatus.REPORTED_COMPLETE:
            state = "<span class='tag bad'>Reported complete - NOT verified</span>"
        else:
            state = "<span class='tag warn'>Not verified</span>"
        out.append(
            f"<h3>{_cell(commitment['title'])} {state}</h3><div class='muted'>{escape(commitment['id'])} &middot; "
            f"reported status: {escape(commitment['reported_status'])} &middot; "
            f"{commitment['criteria_verified']} of {commitment['criteria_total']} criteria verified &middot; "
            f"expected deliverable: {_cell(commitment['expected_deliverable'])}</div>"
        )
        if not commitment["criteria"]:
            out.append("<p class='muted'>No acceptance criteria defined; this commitment cannot be verified.</p>")
        for criterion in commitment["criteria"]:
            review = criterion["review"]
            out.append(
                f"<div class='criterion'><strong>{_cell(criterion['text'])}</strong> {_tag(criterion['status'])}"
                f"<div class='muted'>{escape(criterion['id'])}</div><p>{_cell(criterion['explanation'])}</p>"
            )
            if review["decision"] != ReviewDecision.NONE:
                out.append(
                    f"<p class='muted'>Human review: {escape(review['decision'])} by {_cell(review['by'])} "
                    f"on {_cell(review['at'])}. Note: {_cell(review['reason'])}</p>"
                )
            if criterion["evidence"]:
                out.append("<table><tr><th>Evidence</th><th>Type</th><th>Description</th><th>SHA-256</th>"
                           "<th>Validation</th><th>In package</th></tr>")
                for evidence in criterion["evidence"]:
                    reference = evidence["original_name"] or evidence["url"]
                    out.append(
                        f"<tr><td>{escape(evidence['id'])}<br><span class='muted'>{_cell(reference)}</span></td>"
                        f"<td>{escape(evidence['type'])}</td><td>{_cell(evidence['description'])}<br>"
                        f"<span class='muted'>by {_cell(evidence['submitted_by'])}, {_cell(evidence['submitted_at'])}</span></td>"
                        f"<td><code>{escape(evidence['sha256'])}</code></td>"
                        f"<td><strong>{escape(evidence['validity'])}</strong><br>{_cell(evidence['validation_message'])}</td>"
                        f"<td>{_cell(evidence['package_path'])}</td></tr>"
                    )
                out.append("</table>")
            else:
                out.append("<p class='muted'>No evidence linked.</p>")
            out.append("</div>")

    out.append("<h2>Review and change history</h2>")
    if manifest["review_history"]:
        out.append("<table><tr><th>#</th><th>Time (UTC)</th><th>Action</th><th>Entity</th><th>From</th><th>To</th>"
                   "<th>Actor label</th><th>Reason</th></tr>")
        for entry in manifest["review_history"]:
            out.append(
                f"<tr><td>{entry['seq']}</td><td>{_cell(entry['at'])}</td><td>{_cell(entry['action'])}</td>"
                f"<td>{_cell(entry['entity_id'])}</td><td>{_cell(entry['previous_state'])}</td>"
                f"<td>{_cell(entry['new_state'])}</td><td>{_cell(entry['actor'])}</td><td>{_cell(entry['reason'])}</td></tr>"
            )
        out.append("</table>")
    else:
        out.append("<p>No history recorded.</p>")

    out.append("<h2>What this report does and does not establish</h2><ul>")
    out.extend(f"<li>{escape(line)}</li>" for line in manifest["disclaimers"])
    out.append(
        "</ul><footer class='muted'>"
        f"Generated {_cell(package['generated_at'])} by {_cell(package['generated_by'])} with "
        f"{escape(package['generator'])} (rules version {escape(package['rules_version'])}).<br>"
        f"manifest.json SHA-256: <code>{escape(manifest_sha256)}</code></footer></main></body></html>"
    )
    return "".join(out)
