"""Delivery Package: final readiness, customer acceptance and the downloadable package."""

from __future__ import annotations

import streamlit as st

from app.ai.interface import summary_context
from app.models import CompletionState, CriterionStatus, CustomerAcceptance
from app.ui.common import PageContext, delivery_banner, md, run_action, show_advice


def _acceptance(ctx: PageContext) -> None:
    project, approvals = ctx.project, ctx.services.approvals
    st.subheader("Customer acceptance")
    st.caption("Recorded explicitly by a person. Internal verification never sets it automatically, and "
               "it is cleared if the delivery changes afterwards.")
    if project.acceptance is not CustomerAcceptance.NOT_RECORDED:
        st.write(
            f"**{project.acceptance}** - recorded by {md(project.acceptance_by)} at {project.acceptance_at}"
            + (f" - {md(project.acceptance_note)}" if project.acceptance_note else "")
        )
        with st.form("clear_acceptance", clear_on_submit=True, border=False):
            reason = st.text_input("Reason for clearing", max_chars=500)
            if st.form_submit_button("Clear recorded acceptance"):
                run_action(lambda: approvals.clear_acceptance(project.id, ctx.user, reason),
                           "Customer acceptance cleared.")
        return
    st.write("**NOT_RECORDED**")
    verified = ctx.assessment.completion_state is CompletionState.INTERNALLY_VERIFIED
    if not verified:
        st.caption("Acceptance can be recorded once every criterion is verified. A decline can be recorded any time.")
    with st.form("record_acceptance", clear_on_submit=True, border=False):
        note = st.text_input("How was the customer's response received?", max_chars=500,
                             placeholder="e.g. Email from sponsor, 12 March (required when declining)")
        accept, decline = st.columns(2)
        if accept.form_submit_button("Record customer acceptance", type="primary", disabled=not verified,
                                     key="record_acceptance_btn"):
            run_action(lambda: approvals.record_acceptance(project.id, True, ctx.user, note),
                       "Customer acceptance recorded.")
        if decline.form_submit_button("Record customer decline"):
            run_action(lambda: approvals.record_acceptance(project.id, False, ctx.user, note),
                       "Customer decline recorded.")


def render(ctx: PageContext) -> None:
    st.header("Delivery Package")
    st.caption(f"Project: {md(ctx.project.name)}")
    delivery_banner(ctx)
    assessment = ctx.assessment
    cols = st.columns(3)
    cols[0].metric("Readiness", f"{assessment.readiness_pct:g}%", border=True)
    cols[1].metric("Verified criteria", f"{assessment.verified} / {assessment.total}", border=True)
    cols[2].metric("Outstanding items", assessment.total - assessment.verified, border=True)

    st.subheader("Outstanding items")
    titles = {c.id: c.text for criteria in ctx.criteria.values() for c in criteria}
    commitment_titles = {c.id: commitment.title for commitment in ctx.commitments for c in ctx.criteria[commitment.id]}
    outstanding = [f for f in assessment.findings if f.status is not CriterionStatus.VERIFIED]
    if outstanding:
        st.dataframe(
            [{"Commitment": commitment_titles[f.criterion_id], "Criterion": titles[f.criterion_id],
              "Status": str(f.status), "Next action": f.next_action} for f in outstanding],
            hide_index=True, width="stretch",
        )
    elif assessment.total:
        st.success("No outstanding verification items.", icon=":material/task_alt:")
    else:
        st.info("No acceptance criteria are defined.")

    _acceptance(ctx)

    st.subheader("Handover wording (optional)")
    if st.button("Draft delivery summary", key="draft_summary", icon=":material/smart_toy:"):
        st.session_state[f"summary_{ctx.project.id}"] = ctx.assistant.draft_delivery_summary(
            summary_context(ctx.project, assessment, titles))
    summary = st.session_state.get(f"summary_{ctx.project.id}")
    if summary:
        show_advice(summary, ctx.assistant.label)

    st.subheader("Generate package")
    complete = assessment.completion_state is CompletionState.INTERNALLY_VERIFIED
    if not complete:
        st.warning("This delivery is not verified. You can still export it, but the report, manifest and "
                   "file name will be labelled INCOMPLETE and will list every outstanding item.",
                   icon=":material/warning:")
    package_key = f"package_{ctx.project.id}"
    if st.button("Generate delivery package", type="primary", key="generate_package", icon=":material/inventory_2:"):
        def build() -> object:
            package = ctx.services.export.build(ctx.project.id, ctx.user)
            st.session_state[package_key] = package
            return package
        run_action(build, lambda package: f"Delivery package generated: {package.filename}")

    package = st.session_state.get(package_key)
    if package:
        meta = package.manifest["package"]
        st.write(f"**{md(package.filename)}** · generated {meta['generated_at']} · {md(meta['headline'])}")
        st.download_button("Download ZIP", package.content, file_name=package.filename, mime="application/zip",
                           key="download_package", icon=":material/download:")
        evidence_paths = [
            evidence["package_path"]
            for commitment in package.manifest["commitments"]
            for criterion in commitment["criteria"]
            for evidence in criterion["evidence"]
            if evidence["package_path"]
        ]
        st.caption("Contents: report.html (human-readable report), manifest.json (evidence manifest with "
                   f"SHA-256 hashes) and {len(evidence_paths)} evidence file(s) under evidence/.")
        with st.expander("Manifest preview"):
            st.json(package.manifest, expanded=1)
