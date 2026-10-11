"""Verification & Approval: automated findings next to, but distinct from, human decisions."""

from __future__ import annotations

import streamlit as st

from app.ai.interface import finding_context
from app.models import Commitment, Criterion, CriterionStatus, ReviewDecision
from app.ui.common import PageContext, badge, md, run_action, show_advice, validity_badge


def _automated(ctx: PageContext, commitment: Commitment, criterion: Criterion) -> None:
    finding = ctx.finding(criterion.id)
    st.markdown("**Automated checks** (deterministic rules)")
    st.write(md(finding.explanation))
    if finding.missing:
        st.markdown("Still required:")
        for gap in finding.missing:
            st.markdown(f"- {md(gap)}")
    for check in finding.checks:
        st.markdown(f"- `{check.evidence_id}` {validity_badge(check.validity)} {md(check.message)}")
    if finding.status is not CriterionStatus.VERIFIED:
        st.markdown(f"**Next action:** {md(finding.next_action)}")
        if st.button("Draft follow-up message", key=f"draft_{criterion.id}", icon=":material/smart_toy:"):
            evidence = ctx.services.repo.list_evidence(criterion_id=criterion.id)
            st.session_state[f"advice_{criterion.id}"] = ctx.assistant.draft_follow_up(
                finding_context(commitment, criterion, finding, evidence))
    advice = st.session_state.get(f"advice_{criterion.id}")
    if advice:
        show_advice(advice, ctx.assistant.label)


def _human(ctx: PageContext, criterion: Criterion) -> None:
    finding = ctx.finding(criterion.id)
    approvals = ctx.services.approvals
    st.markdown("**Human review** (decision by a person)")
    if not criterion.requires_human_review:
        st.caption("This criterion is configured without human review.")
    if criterion.decision is not ReviewDecision.NONE and not finding.decision_stale:
        st.write(
            f"{criterion.decision} by {md(criterion.decision_by)} at {criterion.decision_at}"
            + (f" - {md(criterion.decision_reason)}" if criterion.decision_reason else "")
        )
    can_approve = finding.status is CriterionStatus.PENDING_HUMAN_REVIEW
    can_reject = finding.status in (CriterionStatus.PENDING_HUMAN_REVIEW, CriterionStatus.VERIFIED)
    can_revoke = criterion.decision is not ReviewDecision.NONE
    if not (can_approve or can_reject or can_revoke):
        st.caption("Review opens once the automated checks pass. Approval cannot override a failed check.")
        return
    with st.form(f"review_{criterion.id}", clear_on_submit=True, border=False):
        reason = st.text_input("Note / reason", max_chars=500, key=f"reason_{criterion.id}",
                               placeholder="Required to reject or revoke")
        buttons = st.columns(3)
        if can_approve and buttons[0].form_submit_button("Approve", type="primary", key=f"approve_{criterion.id}"):
            run_action(lambda: approvals.approve(criterion.id, ctx.user, reason), "Criterion approved.")
        if can_reject and buttons[1].form_submit_button("Reject", key=f"reject_{criterion.id}"):
            run_action(lambda: approvals.reject(criterion.id, ctx.user, reason), "Criterion rejected.")
        if can_revoke and buttons[2].form_submit_button("Revoke decision", key=f"revoke_{criterion.id}"):
            run_action(lambda: approvals.revoke(criterion.id, ctx.user, reason), "Review decision revoked.")
    st.caption(f"Recorded under the local label '{md(ctx.user)}'. Not an authenticated identity or e-signature.")


def render(ctx: PageContext) -> None:
    st.header("Verification & Approval")
    st.caption(f"Project: {md(ctx.project.name)}")
    runs = ctx.services.repo.list_runs(ctx.project.id)
    run_col, info_col = st.columns([1, 3])
    if run_col.button("Run verification", type="primary", key="run_verification", icon=":material/fact_check:"):
        def execute() -> str:
            ctx.services.verification.run(ctx.project.id)
            latest = ctx.services.repo.list_runs(ctx.project.id)[0]
            return (f"Verification run #{latest['id']} completed in {latest['duration_ms']} ms: "
                    f"{latest['verified']} of {latest['total']} criteria verified.")
        run_action(execute, lambda message: message)
    if runs:
        info_col.caption(
            f"Last recorded run: #{runs[0]['id']} at {runs[0]['run_at']} ({runs[0]['duration_ms']} ms, "
            f"rules v{runs[0]['rules_version']}). Findings below are always recomputed from current evidence."
        )
    else:
        info_col.caption("No verification run recorded yet. Findings below are computed from current evidence; "
                         "running verification records a timestamped run and its metrics.")

    if not ctx.assessment.total:
        st.info("There are no acceptance criteria to verify yet.")
        return

    for commitment in ctx.commitments:
        head, action = st.columns([5, 1])
        head.subheader(md(commitment.title))
        head.caption(f"Reported status: {commitment.reported_status}")
        with action.popover("Reopen", icon=":material/restart_alt:", key=f"reopen_pop_{commitment.id}"):
            st.caption("Clears every review decision under this commitment and marks it IN_PROGRESS.")
            with st.form(f"reopen_{commitment.id}", clear_on_submit=True):
                reason = st.text_input("Reason", max_chars=500)
                if st.form_submit_button("Reopen commitment"):
                    run_action(lambda: ctx.services.approvals.reopen_commitment(commitment.id, ctx.user, reason),
                               "Commitment reopened.")
        for criterion in ctx.criteria[commitment.id]:
            finding = ctx.finding(criterion.id)
            with st.expander(f"{badge(finding.status)} {md(criterion.text)}",
                             expanded=finding.status is not CriterionStatus.VERIFIED):
                with st.container(key=f"criterion_{criterion.id}"):
                    automated, human = st.columns([3, 2])
                    with automated:
                        _automated(ctx, commitment, criterion)
                    with human:
                        _human(ctx, criterion)
                    history = ctx.services.repo.list_audit(ctx.project.id, criterion.id)
                    if history:
                        st.markdown("**Review history**")
                        st.dataframe(
                            [{"Time (UTC)": h.at, "Action": h.action, "From": h.previous_state, "To": h.new_state,
                              "Actor label": h.actor, "Reason": h.reason} for h in history],
                            hide_index=True, width="stretch",
                        )
