"""ProofFlow AI - Streamlit entry point.

Run with:  streamlit run app/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # `streamlit run` puts app/ on the path, not the repository root
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import __version__, demo_data  # noqa: E402
from app.ai import build_assistant  # noqa: E402
from app.ai.interface import Assistant  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.observability import configure_logging  # noqa: E402
from app.services import Services, build_services  # noqa: E402
from app.ui import commitments, delivery, evidence, overview, verification  # noqa: E402
from app.ui.common import PageContext, run_action, show_flash  # noqa: E402

SECTIONS = {
    "Overview": overview.render,
    "Commitments": commitments.render,
    "Evidence Workspace": evidence.render,
    "Verification & Approval": verification.render,
    "Delivery Package": delivery.render,
}


@st.cache_resource
def get_services() -> Services:
    configure_logging()
    services = build_services(load_settings())
    if services.settings.seed_demo and not services.repo.list_projects():
        demo_data.seed_before(services)
    return services


@st.cache_resource
def get_assistant() -> Assistant:
    return build_assistant(load_settings())


def _demo_controls(services: Services, project_id: str | None) -> None:
    with st.sidebar.expander("Demo scenario", icon=":material/science:"):
        st.caption("Synthetic 'Customer Analytics Dashboard' delivery. Approvals stay manual on purpose.")
        demo = demo_data.find_demo_project(services)
        if st.button("Reset demo to 'Before' state", key="demo_reset", width="stretch"):
            def reset() -> None:
                st.session_state["pending_project_id"] = demo_data.seed_before(services).id
            run_action(reset, "Demo project reset to its 'Before' state.")
        if st.button("Apply 'After' evidence", key="demo_after", width="stretch",
                     disabled=demo is None or demo.id != project_id):
            run_action(lambda: demo_data.apply_after(services, project_id),
                       lambda steps: f"Demo evidence applied ({len(steps)} changes). Review and approve next.")


def _diagnostics(services: Services, project_id: str) -> None:
    with st.sidebar.expander("Diagnostics", icon=":material/monitor_heart:"):
        runs = services.repo.list_runs(project_id)
        actions = [entry.action for entry in services.repo.list_audit(project_id)]
        st.caption(
            f"Database: {'ok' if services.repo.ping() else 'unavailable'}  \n"
            f"Evidence store: {'writable' if services.store.is_writable() else 'NOT writable'}  \n"
            f"Verification runs: {len(runs)}  \n"
            f"Last run duration: {str(runs[0]['duration_ms']) + ' ms' if runs else 'n/a'}  \n"
            f"Exports generated: {actions.count('EXPORT_GENERATED')}  \n"
            f"Export failures: {actions.count('EXPORT_FAILED')}  \n"
            f"Version: {__version__}"
        )


def main() -> None:
    st.set_page_config(page_title="ProofFlow AI", page_icon=":material/fact_check:", layout="wide")
    services, assistant = get_services(), get_assistant()

    st.sidebar.title("ProofFlow AI")
    st.sidebar.caption("Evidence-based delivery & completion assurance")

    projects = {p.id: p for p in services.repo.list_projects()}
    pending = st.session_state.pop("pending_project_id", None)
    if pending in projects:
        st.session_state["project_id"] = pending
    if st.session_state.get("project_id") not in projects:
        st.session_state.pop("project_id", None)

    if not projects:
        show_flash()
        st.header("Welcome to ProofFlow AI")
        st.info("There are no projects yet. Create one below, or load the synthetic demo from the sidebar.")
        commitments.render_new_project_form(services, key="first_project")
        _demo_controls(services, None)
        return

    project_id = st.sidebar.selectbox("Active project", list(projects), key="project_id",
                                      format_func=lambda pid: projects[pid].name)
    section = st.sidebar.radio("Section", list(SECTIONS), key="section")
    user = st.sidebar.text_input("Your name or role", value="Demo Reviewer", max_chars=80, key="user_label",
                                 help="A local label recorded with your actions. It is not an authenticated identity.")
    st.sidebar.caption(f":material/smart_toy: {assistant.label}")
    _demo_controls(services, project_id)
    _diagnostics(services, project_id)
    st.sidebar.caption("Local POC. Use synthetic data only; do not expose this app publicly.")

    project_commitments = services.repo.list_commitments(project_id)
    context = PageContext(
        services=services,
        assistant=assistant,
        project=projects[project_id],
        user=user,
        assessment=services.verification.assess(project_id),
        commitments=project_commitments,
        criteria={c.id: services.repo.list_criteria(c.id) for c in project_commitments},
    )
    show_flash()
    SECTIONS[section](context)


main()
