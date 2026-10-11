"""Export status wording and HTML report escaping."""

from __future__ import annotations

from app.models import CompletionState, CustomerAcceptance
from app.services.export_service import headline, render_report


def test_headline_never_overstates_completion_or_acceptance():
    acceptance = CustomerAcceptance
    assert headline(CompletionState.NOT_READY, acceptance.NOT_RECORDED, 1, 7).startswith("INCOMPLETE")
    assert headline(CompletionState.AWAITING_REVIEW, acceptance.NOT_RECORDED, 0, 7).startswith("INCOMPLETE")
    assert headline(CompletionState.NO_CRITERIA, acceptance.NOT_RECORDED, 0, 0).startswith("INCOMPLETE")
    assert headline(CompletionState.INTERNALLY_VERIFIED, acceptance.NOT_RECORDED, 7, 7) == \
        "Internally verified - customer acceptance NOT recorded"
    assert "DECLINED" in headline(CompletionState.INTERNALLY_VERIFIED, acceptance.DECLINED, 7, 7)
    assert headline(CompletionState.INTERNALLY_VERIFIED, acceptance.ACCEPTED, 7, 7) == \
        "Internally verified - customer acceptance recorded"


def test_an_accepted_flag_cannot_make_an_unverified_delivery_look_complete():
    assert headline(CompletionState.NOT_READY, CustomerAcceptance.ACCEPTED, 6, 7).startswith("INCOMPLETE")


def test_report_escapes_every_user_controlled_field():
    payload = "<script>alert('x')</script><img src=x onerror=alert(1)>"
    manifest = {
        "package": {"complete": False, "headline": payload, "generated_at": payload, "generated_by": payload,
                    "generator": payload, "rules_version": payload},
        "project": {"id": payload, "name": payload, "customer": payload, "description": payload,
                    "delivery_date": payload, "status": "ACTIVE"},
        "completion": {"state": "NOT_READY", "readiness_pct": 0.0, "criteria_verified": 0, "criteria_total": 1},
        "customer_acceptance": {"state": "DECLINED", "recorded_by": payload, "recorded_at": payload, "note": payload},
        "commitments": [{
            "id": payload, "title": payload, "description": payload, "expected_deliverable": payload,
            "reported_status": "REPORTED_COMPLETE", "due_date": None, "criteria_verified": 0, "criteria_total": 1,
            "verified": False,
            "criteria": [{
                "id": payload, "text": payload, "status": "REJECTED", "explanation": payload, "missing": [payload],
                "next_action": payload,
                "review": {"decision": "REJECTED", "by": payload, "at": payload, "reason": payload},
                "evidence": [{"id": payload, "type": "LINK", "description": payload, "original_name": None,
                              "url": "https://example.com/?q=" + payload, "sha256": payload, "size_bytes": 0,
                              "submitted_by": payload, "submitted_at": payload, "validity": "UNVERIFIED_REFERENCE",
                              "validation_message": payload, "package_path": None}],
            }],
        }],
        "outstanding_items": [{"commitment": payload, "criterion": payload, "status": "REJECTED",
                               "explanation": payload, "missing": [payload], "next_action": payload}],
        "review_history": [{"seq": 1, "at": payload, "action": payload, "entity_id": payload,
                            "previous_state": payload, "new_state": payload, "actor": payload, "reason": payload}],
        "disclaimers": [payload],
    }
    html = render_report(manifest, payload)
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html
    assert "Content-Security-Policy" in html
    assert "href=" not in html  # links are shown as text, never as clickable anchors
