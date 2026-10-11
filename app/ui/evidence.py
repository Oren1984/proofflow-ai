"""Evidence Workspace: attach evidence to acceptance criteria and see how it validates."""

from __future__ import annotations

import json

import streamlit as st

from app.models import CriterionStatus, Evidence, EvidenceCheck, EvidenceType, EvidenceValidity
from app.rules import TYPE_LABELS
from app.security import ALLOWED_EXTENSIONS
from app.ui.common import PageContext, badge, md, run_action, validity_badge

PREVIEW_CHARS = 2000


def _preview(ctx: PageContext, evidence: Evidence, check: EvidenceCheck) -> None:
    if evidence.type is EvidenceType.LINK:
        st.code(evidence.url, language=None, wrap_lines=True)
        st.caption("Recorded as an unverified reference. ProofFlow never opens or fetches links.")
        return
    if check.validity is EvidenceValidity.INVALID:
        st.caption("No preview: this file failed validation.")
        return
    content = ctx.services.evidence.read(evidence)
    if content is None:
        st.caption("No preview: the stored file is missing.")
    elif evidence.type is EvidenceType.SCREENSHOT:
        st.image(content, width=480)
    elif evidence.type is EvidenceType.TEST_RESULT:
        st.json(json.loads(content.decode("utf-8")))
    elif evidence.type is EvidenceType.TEXT:
        text = content.decode("utf-8")
        st.code(text[:PREVIEW_CHARS], language=None, wrap_lines=True)
        if len(text) > PREVIEW_CHARS:
            st.caption(f"Preview truncated to {PREVIEW_CHARS} characters.")
    else:
        st.caption("PDF preview is not rendered here. The file is included in the delivery package.")


def render(ctx: PageContext) -> None:
    st.header("Evidence Workspace")
    st.caption(f"Project: {md(ctx.project.name)}")
    criteria = {c.id: (commitment, c) for commitment in ctx.commitments for c in ctx.criteria[commitment.id]}
    if not criteria:
        st.info("Define commitments and acceptance criteria first; evidence is always linked to a criterion.")
        return

    without_evidence = [c for _, c in criteria.values()
                        if ctx.finding(c.id).status is CriterionStatus.MISSING_EVIDENCE]
    if without_evidence:
        st.warning(
            f"{len(without_evidence)} acceptance criteria have no evidence: "
            + "; ".join(md(c.text) for c in without_evidence),
            icon=":material/report:",
        )

    if st.session_state.get("evidence_criterion") not in criteria:
        st.session_state.pop("evidence_criterion", None)
    criterion_id = st.selectbox(
        "Acceptance criterion", list(criteria), key="evidence_criterion",
        format_func=lambda cid: f"{criteria[cid][0].title} - {criteria[cid][1].text}  [{ctx.finding(cid).status}]",
    )
    commitment, criterion = criteria[criterion_id]
    finding = ctx.finding(criterion_id)
    checks = {check.evidence_id: check for check in finding.checks}

    add_col, status_col = st.columns([3, 2])
    with status_col.container(border=True):
        st.markdown(f"{badge(finding.status)}")
        st.write(md(finding.explanation))
        for gap in finding.missing:
            st.markdown(f"- {md(gap)}")
        accepted = ", ".join(TYPE_LABELS[t] for t in criterion.allowed_types)
        st.caption(f"Accepted evidence: {accepted}. Minimum valid items: {criterion.min_evidence}.")

    with add_col:
        tab_file, tab_link = st.tabs(["Upload file", "Record external link"])
        with tab_file, st.form("upload_evidence", clear_on_submit=True):
            limit_mb = ctx.services.settings.max_upload_bytes / 1_048_576
            uploaded = st.file_uploader(
                f"Evidence file (max {limit_mb:g} MB)", type=[ext.lstrip(".") for ext in ALLOWED_EXTENSIONS],
                help="PNG/JPEG screenshots, PDF documents, TXT/Markdown files, or a JSON test-result record.",
            )
            description = st.text_input("What does this evidence show?", max_chars=500)
            if st.form_submit_button("Attach evidence", type="primary"):
                if uploaded is None:
                    st.error("Choose a file to upload.")
                else:
                    run_action(
                        lambda: ctx.services.evidence.add_file(
                            criterion_id, uploaded.name, uploaded.getvalue(), description, ctx.user),
                        lambda evidence: f"Evidence {evidence.id} attached. Earlier review decisions for this "
                                         "criterion no longer apply.",
                    )
        with tab_link, st.form("link_evidence", clear_on_submit=True):
            url = st.text_input("URL", placeholder="https://...")
            link_description = st.text_input("What does this link refer to?", max_chars=500)
            st.caption("Links are recorded as unverified references. They are never fetched and never "
                       "satisfy a criterion on their own.")
            if st.form_submit_button("Record link"):
                run_action(
                    lambda: ctx.services.evidence.add_link(criterion_id, url, link_description, ctx.user),
                    lambda evidence: f"Link {evidence.id} recorded as an unverified reference.",
                )

    st.subheader("Evidence for this criterion")
    linked = ctx.services.repo.list_evidence(criterion_id=criterion_id)
    if not linked:
        st.info("No evidence is linked to this criterion yet.")
    for evidence in linked:
        check = checks[evidence.id]
        with st.container(border=True, key=f"evidence_{evidence.id}"):
            info, action = st.columns([6, 1])
            info.markdown(
                f"**{md(evidence.original_name or 'External link')}** · {TYPE_LABELS[evidence.type]} · "
                f"{validity_badge(check.validity)}"
            )
            info.write(md(check.message))
            info.caption(
                f"{evidence.id} · {md(evidence.description)} · submitted by {md(evidence.submitted_by)} "
                f"at {evidence.submitted_at} · SHA-256 `{evidence.sha256[:16]}…`"
            )
            if action.button("Remove", key=f"remove_{evidence.id}", icon=":material/delete:"):
                run_action(lambda: ctx.services.evidence.remove(evidence.id, ctx.user, "Removed in Evidence Workspace"),
                           f"Evidence {evidence.id} removed.")
            with st.expander("Preview"):
                _preview(ctx, evidence, check)

    st.subheader("All evidence in this project")
    all_checks = {check.evidence_id: check for f in ctx.assessment.findings for check in f.checks}
    rows = [
        {
            "Evidence": evidence.id,
            "Name": evidence.original_name or evidence.url,
            "Type": TYPE_LABELS[evidence.type],
            "Criterion": criteria[evidence.criterion_id][1].text,
            "Validation": str(all_checks[evidence.id].validity),
            "SHA-256": evidence.sha256[:16] + "…",
            "Submitted": evidence.submitted_at,
        }
        for evidence in ctx.services.repo.list_evidence(project_id=ctx.project.id)
    ]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("No evidence has been submitted for this project.")
