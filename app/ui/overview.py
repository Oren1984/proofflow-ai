"""Overview: where the delivery actually stands."""

from __future__ import annotations

import streamlit as st

from app.models import CriterionStatus, ReportedStatus
from app.ui.common import PageContext, delivery_banner, md


def render(ctx: PageContext) -> None:
    project, assessment = ctx.project, ctx.assessment
    st.header(md(project.name))
    st.caption(
        f"Customer: {md(project.customer)} · Planned delivery: {md(project.delivery_date or 'not set')} · "
        f"Project status: {project.status}"
    )
    if project.description:
        st.write(md(project.description))
    delivery_banner(ctx)

    counts = assessment.counts
    blocked = (counts[CriterionStatus.MISSING_EVIDENCE] + counts[CriterionStatus.INVALID_EVIDENCE]
               + counts[CriterionStatus.INSUFFICIENT_EVIDENCE])
    cols = st.columns([1, 1.1, 1.6, 1.3, 1])
    cols[0].metric("Commitments", len(ctx.commitments), border=True)
    cols[1].metric("Verified criteria", f"{assessment.verified} / {assessment.total}", border=True)
    cols[2].metric("Missing or invalid evidence", blocked, border=True,
                   help="Criteria whose evidence is missing, invalid or insufficient.")
    cols[3].metric("Pending human review", counts[CriterionStatus.PENDING_HUMAN_REVIEW], border=True)
    cols[4].metric("Readiness", f"{assessment.readiness_pct:g}%", border=True,
                   help="Verified criteria / total criteria x 100. Every criterion counts equally.")
    st.progress(
        min(assessment.readiness_pct / 100, 1.0),
        text=f"Readiness: {assessment.verified} of {assessment.total} acceptance criteria verified",
    )

    left, right = st.columns(2)
    with left.container(border=True):
        st.caption("Internal completion (derived from evidence and review)")
        st.subheader(str(assessment.completion_state))
    with right.container(border=True):
        st.caption("Customer acceptance (recorded separately by a person)")
        st.subheader(str(project.acceptance))
    st.caption("A readiness of 100% means the delivery is internally verified. It never implies customer acceptance.")

    st.subheader("Reported vs. verified")
    rows, reported_complete, verified_commitments = [], 0, 0
    for commitment in ctx.commitments:
        criteria = ctx.criteria[commitment.id]
        verified = sum(1 for c in criteria if ctx.finding(c.id).status is CriterionStatus.VERIFIED)
        is_verified = bool(criteria) and verified == len(criteria)
        is_reported = commitment.reported_status is ReportedStatus.REPORTED_COMPLETE
        reported_complete += is_reported
        verified_commitments += is_verified
        if is_verified:
            conclusion = "Verified"
        elif is_reported:
            conclusion = "Reported complete - NOT verified"
        else:
            conclusion = "Not verified"
        rows.append({
            "Commitment": commitment.title,
            "Reported status": str(commitment.reported_status),
            "Criteria verified": f"{verified} of {len(criteria)}",
            "Evidence-based conclusion": conclusion,
        })
    if rows:
        st.write(
            f"**{reported_complete} of {len(rows)}** commitments are reported complete. "
            f"**{verified_commitments} of {len(rows)}** are verified by evidence and human review."
        )
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("This project has no commitments yet. Add them in the Commitments section.")
