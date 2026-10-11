"""Shared UI helpers. User-controlled text is always escaped before it reaches Markdown."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from app.ai.interface import Advice, Assistant
from app.models import (
    Assessment,
    Commitment,
    CompletionState,
    Criterion,
    CriterionStatus,
    CustomerAcceptance,
    EvidenceValidity,
    Finding,
    Project,
    ValidationError,
)
from app.observability import log_event
from app.services import Services
from app.services.export_service import headline

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")

STATUS_STYLE = {
    CriterionStatus.VERIFIED: ("green", ":material/verified:"),
    CriterionStatus.PENDING_HUMAN_REVIEW: ("orange", ":material/hourglass_top:"),
    CriterionStatus.MISSING_EVIDENCE: ("red", ":material/report:"),
    CriterionStatus.INVALID_EVIDENCE: ("red", ":material/cancel:"),
    CriterionStatus.INSUFFICIENT_EVIDENCE: ("red", ":material/rule:"),
    CriterionStatus.REJECTED: ("red", ":material/thumb_down:"),
}

VALIDITY_COLOR = {
    EvidenceValidity.VALID: "green",
    EvidenceValidity.INVALID: "red",
    EvidenceValidity.STALE: "orange",
    EvidenceValidity.UNVERIFIED_REFERENCE: "orange",
}


@dataclass
class PageContext:
    services: Services
    assistant: Assistant
    project: Project
    user: str
    assessment: Assessment
    commitments: list[Commitment]
    criteria: dict[str, list[Criterion]]  # commitment id -> criteria

    def finding(self, criterion_id: str) -> Finding:
        return self.assessment.finding_for(criterion_id)


def md(value: object) -> str:
    """Escape untrusted text for inline Markdown (prevents injected links, images and directives)."""
    return _MD_SPECIAL.sub(r"\\\1", str(value or "")).replace("\n", " ")


def badge(status: CriterionStatus) -> str:
    color, icon = STATUS_STYLE[status]
    return f":{color}-badge[{icon} {status}]"


def validity_badge(validity: EvidenceValidity) -> str:
    return f":{VALIDITY_COLOR[validity]}-badge[{validity}]"


def show_flash() -> None:
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message, icon=":material/check_circle:")


def run_action(action: Callable[[], object], success: str | Callable[[object], str]) -> None:
    """Run a state-changing action; show validation errors inline, otherwise flash and rerun."""
    try:
        result = action()
    except ValidationError as exc:
        st.error(md(str(exc)), icon=":material/error:")
        return
    except Exception:  # noqa: BLE001 - last-resort guard so the UI stays usable
        log_event("ui.unexpected_error", level=logging.ERROR, exc_info=True)
        st.error("Something went wrong. The error was logged and nothing was confirmed as saved.")
        return
    st.session_state["flash"] = success(result) if callable(success) else success
    st.rerun()


def delivery_banner(ctx: PageContext) -> None:
    """Current delivery status. Completion and customer acceptance are always shown separately."""
    assessment, acceptance = ctx.assessment, ctx.project.acceptance
    text = headline(assessment.completion_state, acceptance, assessment.verified, assessment.total)
    state = assessment.completion_state
    if state is CompletionState.NO_CRITERIA:
        st.info("No acceptance criteria are defined yet, so nothing can be verified.", icon=":material/info:")
    elif state is CompletionState.NOT_READY:
        st.error(text, icon=":material/gpp_bad:")
    elif state is CompletionState.AWAITING_REVIEW or acceptance is not CustomerAcceptance.ACCEPTED:
        st.warning(text, icon=":material/pending_actions:")
    else:
        st.success(text, icon=":material/verified:")


def show_advice(advice: Advice, assistant_label: str) -> None:
    source = "Simulated assistance (Mock - deterministic templates, not an LLM)" if advice.simulated else assistant_label
    if advice.fallback_reason:
        source += f" - {advice.fallback_reason}; used the Mock fallback"
    st.caption(f":material/smart_toy: {md(source)}. A drafting suggestion only - it does not change any status.")
    st.code(advice.text, language=None, wrap_lines=True)
