"""Deterministic, offline assistant. Template-based simulated assistance - not an LLM."""

from __future__ import annotations

from app.ai.interface import Advice

_ASK = {
    "MISSING_EVIDENCE": "nothing has been attached for it yet",
    "INVALID_EVIDENCE": "the attached evidence did not pass validation",
    "INSUFFICIENT_EVIDENCE": "the attached evidence does not yet meet the agreed evidence requirements",
    "PENDING_HUMAN_REVIEW": "the evidence passed automated checks and now needs a reviewer's decision",
    "REJECTED": "the reviewer rejected the submitted evidence",
    "VERIFIED": "it is verified and needs no further action",
}


class MockAssistant:
    label = "Mock assistant (simulated, deterministic templates - no LLM)"

    def draft_follow_up(self, context: dict) -> Advice:
        refs = tuple(e["id"] for e in context["evidence"])
        lines = [
            f"Hi - a quick follow-up on \"{context['commitment']}\".",
            f"The acceptance criterion \"{context['criterion']}\" cannot be marked verified yet: "
            f"{_ASK[context['status']]}.",
        ]
        if context["missing"]:
            lines.append("What is still needed:")
            lines.extend(f"- {item}" for item in context["missing"])
        if refs:
            lines.append(f"Evidence already on file: {', '.join(refs)}.")
        lines.append(f"Next step: {context['next_action']}")
        return Advice("\n".join(lines), refs, "mock", simulated=True)

    def draft_delivery_summary(self, context: dict) -> Advice:
        outstanding = context["outstanding"]
        lines = [
            f"Delivery summary for {context['project']} ({context['customer']}).",
            f"{context['verified']} of {context['total']} acceptance criteria are verified "
            f"({context['readiness_pct']}% readiness). Internal completion state: {context['completion_state']}.",
        ]
        if outstanding:
            lines.append(f"{len(outstanding)} criteria remain open:")
            lines.extend(f"- {item['criterion']} [{item['status']}]" for item in outstanding)
            lines.append("This delivery should not be presented as complete until these are resolved.")
        else:
            lines.append("All acceptance criteria are supported by validated evidence and reviewer approval.")
        if context["customer_acceptance"] == "ACCEPTED":
            lines.append("Customer acceptance has been recorded.")
        else:
            lines.append("Customer acceptance has not been recorded and is tracked separately.")
        return Advice("\n".join(lines), tuple(item["criterion_id"] for item in outstanding), "mock", simulated=True)
