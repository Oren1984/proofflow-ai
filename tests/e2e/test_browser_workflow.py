"""Browser-level E2E: the main workflow through the real Streamlit UI (Playwright + Chromium).

Starts the app as a subprocess on a free port with an empty temporary data directory,
so it never touches local data. Skipped automatically when Playwright is not installed.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect
TIMEOUT_MS = 60_000  # generous: the suite must stay reliable on a busy machine
expect.set_options(timeout=TIMEOUT_MS)

ROOT = Path(__file__).resolve().parents[2]
DEMO_FILES = ROOT / "demo" / "synthetic_data"
KPI_CRITERION = "Required KPIs (revenue, active customers, churn) are displayed"
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    external = os.environ.get("PROOFFLOW_E2E_URL")  # e.g. a freshly started Docker container
    if external:
        yield external.rstrip("/")
        return
    port = _free_port()
    env = {**os.environ, "PROOFFLOW_DATA_DIR": str(tmp_path_factory.mktemp("e2e-data")), "PROOFFLOW_AI_MODE": "mock"}
    env.pop("ANTHROPIC_API_KEY", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app/main.py", "--server.port", str(port),
         "--server.address", "127.0.0.1", "--server.headless", "true"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("Streamlit did not become healthy in time")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=15)


@pytest.fixture(scope="module")
def page(app_url):
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        context.set_default_timeout(TIMEOUT_MS)
        page = context.new_page()
        page.goto(app_url)
        yield page
        browser.close()


def settle(page) -> None:
    """Wait until Streamlit is not running the script, so the page is not mid-render."""
    expect(page.locator("[data-testid='stApp']")).to_have_attribute("data-test-script-state", "notRunning")


RUN_COUNTER = """() => {
  if (window.__proofflowRuns !== undefined) return;
  window.__proofflowRuns = 0;
  const app = document.querySelector("[data-testid='stApp']");
  new MutationObserver(() => {
    if (app.getAttribute('data-test-script-state') === 'notRunning') window.__proofflowRuns += 1;
  }).observe(app, {attributes: true, attributeFilter: ['data-test-script-state']});
}"""


def act(page, locator) -> None:
    """Click a control that triggers a Streamlit rerun and wait for that rerun to finish.

    Checking "not running" straight after a click is not enough: the rerun starts ~100 ms
    later, so the check can pass before it begins and the next step then races the re-render.
    """
    settle(page)
    page.evaluate(RUN_COUNTER)
    completed = page.evaluate("window.__proofflowRuns")
    locator.click()
    page.wait_for_function("n => window.__proofflowRuns > n", arg=completed)
    settle(page)


def click(page, locator) -> None:
    """Click a purely client-side control (expander, tab, dropdown) once the page is idle."""
    settle(page)
    locator.click()


def go_to(page, section: str) -> None:
    act(page, page.locator("section[data-testid='stSidebar']").get_by_text(section, exact=True))
    heading = "Customer Analytics Dashboard" if section == "Overview" else section
    expect(page.get_by_role("heading", name=heading).first).to_be_visible()


def choose(page, label: str, option_text: str) -> None:
    """Pick a selectbox option by typing to filter; typing opens the list deterministically."""
    box = page.get_by_role("combobox", name=label)
    click(page, box)
    box.fill(option_text[:20])
    option = page.get_by_role("option").filter(has_text=option_text)
    expect(option).to_be_visible()
    act(page, option)


def metric(page, label: str):
    return page.locator("[data-testid='stMetric']").filter(has_text=label).locator("[data-testid='stMetricValue']")


def test_main_workflow_from_unverified_to_exported_package(page):
    main = page.locator("section[data-testid='stMain']")

    # 1-2. Open the app; the synthetic project is selected.
    expect(page.get_by_role("heading", name="Customer Analytics Dashboard")).to_be_visible()
    expect(page.locator("section[data-testid='stSidebar']")).to_contain_text("Mock assistant")

    # 3. Inspect the incomplete delivery: reported complete, nothing verified.
    expect(main).to_contain_text("INCOMPLETE - not verified (0 of 7 acceptance criteria verified)")
    expect(metric(page, "Verified criteria")).to_have_text("0 / 7")
    expect(metric(page, "Missing or invalid evidence")).to_have_text("6")
    expect(metric(page, "Readiness")).to_have_text("0%")
    expect(main).to_contain_text("5 of 5 commitments are reported complete")
    expect(main).to_contain_text("NOT_RECORDED")

    # 4-5. Execute verification and view the findings.
    go_to(page, "Verification & Approval")
    act(page, page.locator(".st-key-run_verification button"))
    expect(main).to_contain_text("Verification run #1 completed")
    expect(main).to_contain_text("0 of 7 criteria verified")
    for status in ("MISSING_EVIDENCE", "INVALID_EVIDENCE", "INSUFFICIENT_EVIDENCE", "PENDING_HUMAN_REVIEW"):
        expect(main.get_by_text(status).first).to_be_visible()
    expect(main).to_contain_text("reports 3 failed of 42 tests")
    expect(main).to_contain_text("File names and descriptions are not treated as test execution")
    expect(main).to_contain_text("No evidence is linked to this criterion")

    # The Mock assistant drafts a follow-up and is labelled as simulated.
    act(page, main.get_by_role("button", name="Draft follow-up message").nth(1))
    expect(main).to_contain_text("Simulated assistance (Mock")
    expect(main).to_contain_text("cannot be marked verified yet")

    # 6. Resolve a missing-evidence condition by uploading a real file through the UI.
    go_to(page, "Evidence Workspace")
    expect(main).to_contain_text("2 acceptance criteria have no evidence")
    choose(page, "Acceptance criterion", KPI_CRITERION)
    expect(main).to_contain_text("No evidence is linked to this criterion yet")
    page.locator("[data-testid='stFileUploaderDropzone'] input[type='file']").set_input_files(
        DEMO_FILES / "dashboard_kpis.png")
    expect(main).to_contain_text("dashboard_kpis.png")
    page.get_by_label("What does this evidence show?").fill("KPI page with revenue, customers and churn")
    act(page, page.get_by_role("button", name="Attach evidence"))
    expect(main).to_contain_text("attached. Earlier review decisions")
    expect(main).to_contain_text("1 acceptance criteria have no evidence")
    expect(main).to_contain_text("File integrity and type confirmed")

    # An unsupported upload is rejected by the server-side allowlist as well as the browser filter.
    click(page, page.get_by_role("tab", name="Record external link"))
    page.get_by_label("URL").fill("javascript:alert(1)")
    page.get_by_label("What does this link refer to?").fill("hostile link")
    act(page, page.get_by_role("button", name="Record link"))
    expect(main).to_contain_text("Only http:// and https:// links can be recorded")

    # Supply the remaining 'After' evidence with the demo helper.
    click(page, page.get_by_text("Demo scenario"))
    act(page, page.locator(".st-key-demo_after button"))
    expect(main).to_contain_text("Demo evidence applied (6 changes)")

    # 7. Re-run verification: every criterion now awaits a human decision.
    go_to(page, "Verification & Approval")
    act(page, page.locator(".st-key-run_verification button"))
    expect(main).to_contain_text("Verification run #2 completed")
    expect(main.get_by_text("PENDING_HUMAN_REVIEW")).to_have_count(7)
    expect(main.get_by_text("MISSING_EVIDENCE")).to_have_count(0)

    # Evidence alone is not completion: readiness is still 0% and acceptance cannot be recorded.
    go_to(page, "Delivery Package")
    expect(metric(page, "Readiness")).to_have_text("0%")
    expect(page.locator(".st-key-record_acceptance_btn button")).to_be_disabled()

    # 8. Approve eligible criteria, one decision per criterion.
    go_to(page, "Verification & Approval")
    for remaining in range(7, 0, -1):
        approve = main.get_by_role("button", name="Approve", exact=True)
        expect(approve).to_have_count(remaining)
        act(page, approve.first)
        expect(main.get_by_role("button", name="Approve", exact=True)).to_have_count(remaining - 1)
    expect(main.get_by_text("VERIFIED", exact=False).first).to_be_visible()

    go_to(page, "Overview")
    expect(metric(page, "Verified criteria")).to_have_text("7 / 7")
    expect(metric(page, "Readiness")).to_have_text("100%")
    expect(main).to_contain_text("Internally verified - customer acceptance NOT recorded")
    expect(main).to_contain_text("INTERNALLY_VERIFIED")
    expect(main).to_contain_text("NOT_RECORDED")

    # 9. Generate and download the delivery package.
    go_to(page, "Delivery Package")
    expect(main).to_contain_text("No outstanding verification items")
    act(page, page.locator(".st-key-generate_package button"))
    expect(main).to_contain_text("Delivery package generated")
    with page.expect_download() as download_info:
        page.locator(".st-key-download_package button").click()
    download = download_info.value
    assert download.suggested_filename.startswith("proofflow_PRJ-") and "INCOMPLETE" not in download.suggested_filename

    # 10. Validate the exported report and manifest.
    with zipfile.ZipFile(io.BytesIO(Path(download.path()).read_bytes())) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        report = archive.read("report.html").decode("utf-8")
        evidence = [e for c in manifest["commitments"] for crit in c["criteria"] for e in crit["evidence"]]
        for entry in evidence:
            if entry["package_path"]:
                assert sha256(archive.read(entry["package_path"])).hexdigest() == entry["sha256"]
    assert {"report.html", "manifest.json"} <= set(names)
    assert manifest["package"]["complete"] is True
    assert manifest["completion"]["criteria_verified"] == manifest["completion"]["criteria_total"] == 7
    assert manifest["customer_acceptance"]["state"] == "NOT_RECORDED"
    assert manifest["outstanding_items"] == []
    assert any(e["original_name"] == "dashboard_kpis.png" and e["submitted_by"] == "Demo Reviewer" for e in evidence)
    assert sum(1 for h in manifest["review_history"] if h["action"] == "APPROVED") == 7
    assert "Internally verified - customer acceptance NOT recorded" in report
    assert "Customer acceptance is outstanding" in report

    # Customer acceptance is a separate, explicit step.
    page.get_by_label("How was the customer's response received?").fill("Email from sponsor")
    act(page, page.locator(".st-key-record_acceptance_btn button"))
    expect(main).to_contain_text("Internally verified - customer acceptance recorded")


def test_changing_evidence_after_approval_reopens_the_review(page):
    """Runs after the main workflow: removing approved evidence must drop verification and acceptance."""
    main = page.locator("section[data-testid='stMain']")
    go_to(page, "Evidence Workspace")
    act(page, main.get_by_role("button", name="Remove").first)
    expect(main).to_contain_text("removed.")
    go_to(page, "Overview")
    expect(metric(page, "Verified criteria")).to_have_text("6 / 7")
    expect(main).to_contain_text("INCOMPLETE - not verified (6 of 7 acceptance criteria verified)")
    expect(main).to_contain_text("NOT_RECORDED")
