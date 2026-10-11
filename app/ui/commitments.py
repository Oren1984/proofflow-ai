"""Commitments: manage the project, its commitments and their acceptance criteria."""

from __future__ import annotations

from datetime import date

import streamlit as st

from app.models import Commitment, Criterion, EvidenceType, ProjectStatus, ReportedStatus
from app.rules import TYPE_LABELS
from app.ui.common import PageContext, badge, md, run_action


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _criterion_fields(key: str, criterion: Criterion | None = None) -> dict:
    text = st.text_input("Acceptance criterion", value=criterion.text if criterion else "", max_chars=300,
                         key=f"{key}_text", placeholder="A single, checkable statement")
    types = st.multiselect(
        "Accepted evidence types", list(EvidenceType),
        default=criterion.allowed_types if criterion else [EvidenceType.SCREENSHOT],
        format_func=lambda t: TYPE_LABELS[t].capitalize(), key=f"{key}_types",
    )
    left, right = st.columns(2)
    min_evidence = left.number_input("Minimum valid evidence items", 1, 10,
                                     criterion.min_evidence if criterion else 1, key=f"{key}_min")
    max_age = right.number_input("Maximum evidence age in days (0 = no limit)", 0, 3650,
                                 (criterion.max_age_days or 0) if criterion else 0, key=f"{key}_age")
    passing = st.checkbox("Requires a passing structured test result",
                          value=criterion.requires_passing_test if criterion else False, key=f"{key}_test")
    human = st.checkbox("Requires human review", value=criterion.requires_human_review if criterion else True,
                        key=f"{key}_human")
    return {
        "text": text, "allowed_types": types, "min_evidence": min_evidence,
        "requires_passing_test": passing, "requires_human_review": human, "max_age_days": max_age or None,
    }


def _policy_summary(criterion: Criterion) -> str:
    parts = [f"needs {criterion.min_evidence}+ of: {', '.join(TYPE_LABELS[t] for t in criterion.allowed_types)}"]
    if criterion.requires_passing_test:
        parts.append("passing test result required")
    if criterion.max_age_days:
        parts.append(f"max age {criterion.max_age_days} days")
    parts.append("human review required" if criterion.requires_human_review else "no human review")
    return " · ".join(parts)


def _render_commitment(ctx: PageContext, commitment: Commitment) -> None:
    projects = ctx.services.projects
    with st.container(border=True, key=f"commitment_{commitment.id}"):
        head, edit = st.columns([5, 1])
        head.subheader(md(commitment.title))
        head.caption(
            f"{commitment.id} · reported status: {commitment.reported_status} · "
            f"expected deliverable: {md(commitment.expected_deliverable or 'not set')} · "
            f"due: {md(commitment.due_date or 'not set')}"
        )
        if commitment.description:
            head.write(md(commitment.description))
        with edit.popover("Edit", icon=":material/edit:", width="stretch"):
            with st.form(f"edit_commitment_{commitment.id}"):
                title = st.text_input("Title", commitment.title, max_chars=160)
                description = st.text_area("Description", commitment.description, max_chars=1000)
                deliverable = st.text_input("Expected deliverable", commitment.expected_deliverable, max_chars=300)
                status = st.selectbox("Reported status", list(ReportedStatus),
                                      index=list(ReportedStatus).index(commitment.reported_status))
                due = st.date_input("Due date (optional)", _as_date(commitment.due_date))
                save, delete = st.columns(2)
                if save.form_submit_button("Save", type="primary"):
                    run_action(lambda: projects.update_commitment(
                        commitment.id, title=title, description=description, expected_deliverable=deliverable,
                        reported_status=status, due_date=due), "Commitment updated.")
                if delete.form_submit_button("Delete commitment"):
                    run_action(lambda: projects.delete_commitment(commitment.id),
                               "Commitment and its criteria and evidence were deleted.")

        criteria = ctx.criteria[commitment.id]
        if not criteria:
            st.info("No acceptance criteria yet. A commitment without criteria cannot be verified.")
        for criterion in criteria:
            row, action = st.columns([5, 1])
            row.markdown(f"{badge(ctx.finding(criterion.id).status)} **{md(criterion.text)}**")
            row.caption(f"{criterion.id} · {_policy_summary(criterion)}")
            with action.popover("Edit", icon=":material/tune:", width="stretch", key=f"edit_pop_{criterion.id}"):
                st.caption("Changing the text or policy invalidates earlier review decisions for this criterion.")
                with st.form(f"edit_criterion_{criterion.id}"):
                    values = _criterion_fields(f"crit_{criterion.id}", criterion)
                    save, delete = st.columns(2)
                    if save.form_submit_button("Save", type="primary"):
                        run_action(lambda: projects.update_criterion(criterion.id, **values), "Criterion updated.")
                    if delete.form_submit_button("Delete criterion"):
                        run_action(lambda: projects.delete_criterion(criterion.id),
                                   "Criterion and its evidence were deleted.")

        with st.popover("Add acceptance criterion", icon=":material/add:", key=f"add_pop_{commitment.id}"):
            with st.form(f"add_criterion_{commitment.id}", clear_on_submit=True):
                values = _criterion_fields(f"new_crit_{commitment.id}")
                if st.form_submit_button("Add criterion", type="primary"):
                    run_action(lambda: projects.add_criterion(commitment.id, **values), "Acceptance criterion added.")


def _render_project_details(ctx: PageContext) -> None:
    project, projects = ctx.project, ctx.services.projects
    with st.form("edit_project"):
        st.text_input("Project name", project.name, disabled=True)
        customer = st.text_input("Customer or stakeholder", project.customer, max_chars=120)
        description = st.text_area("Short delivery description", project.description, max_chars=1000)
        left, right = st.columns(2)
        delivery = left.date_input("Delivery date (optional)", _as_date(project.delivery_date))
        status = right.selectbox("Project status", list(ProjectStatus), index=list(ProjectStatus).index(project.status))
        if st.form_submit_button("Save project", type="primary"):
            run_action(lambda: projects.update_project(
                project.id, customer=customer, description=description, delivery_date=delivery, status=status),
                "Project updated.")
    with st.expander("Delete this project", icon=":material/delete:"):
        st.warning("Permanently removes the project, its commitments, evidence files and review history "
                   "from local storage. This cannot be undone.")
        confirm = st.checkbox("I understand - delete this project and its local data", key="confirm_delete")
        if st.button("Delete project", disabled=not confirm):
            run_action(lambda: projects.delete_project(project.id), "Project deleted.")


def render_new_project_form(services, *, key: str = "new_project") -> None:
    with st.form(key, clear_on_submit=True):
        name = st.text_input("Project name", max_chars=120)
        customer = st.text_input("Customer or stakeholder", max_chars=120)
        description = st.text_area("Short delivery description", max_chars=1000)
        left, right = st.columns(2)
        delivery = left.date_input("Delivery date (optional)", None)
        status = right.selectbox("Project status", list(ProjectStatus))
        st.caption("Use synthetic or non-sensitive data only. This is a local demonstration tool.")
        if st.form_submit_button("Create project", type="primary"):
            def create() -> object:
                project = services.projects.create_project(name, customer, description, delivery, status)
                st.session_state["pending_project_id"] = project.id
                return project
            run_action(create, "Project created. Add its commitments next.")


def render(ctx: PageContext) -> None:
    st.header("Commitments")
    st.caption(f"Project: {md(ctx.project.name)}")
    tab_commitments, tab_project, tab_new = st.tabs(["Commitments & criteria", "Project details", "New project"])

    with tab_commitments:
        if not ctx.commitments:
            st.info("No commitments yet. Add the first customer commitment below.")
        for commitment in ctx.commitments:
            _render_commitment(ctx, commitment)
        with st.expander("Add commitment", icon=":material/add:", expanded=not ctx.commitments):
            with st.form("add_commitment", clear_on_submit=True):
                title = st.text_input("Title", max_chars=160, placeholder="e.g. Deliver customer reporting dashboard")
                description = st.text_area("Description", max_chars=1000)
                deliverable = st.text_input("Expected deliverable", max_chars=300)
                left, right = st.columns(2)
                status = left.selectbox("Reported status", list(ReportedStatus))
                due = right.date_input("Due date (optional)", None)
                if st.form_submit_button("Add commitment", type="primary"):
                    run_action(lambda: ctx.services.projects.add_commitment(
                        ctx.project.id, title, description, deliverable, status, due), "Commitment added.")
    with tab_project:
        _render_project_details(ctx)
    with tab_new:
        render_new_project_form(ctx.services)
