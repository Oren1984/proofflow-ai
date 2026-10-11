"""Readiness probe: Streamlit answers, the database opens and the evidence store is writable.

    python -m app.healthcheck        (exit code 0 = ready)
"""

from __future__ import annotations

import os
import sys
import urllib.request

from app.config import load_settings
from app.storage import EvidenceStore, Repository


def check(url: str) -> list[str]:
    problems: list[str] = []
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - fixed local URL
            if response.status != 200:
                problems.append(f"streamlit health returned HTTP {response.status}")
    except OSError as exc:
        problems.append(f"streamlit not reachable: {exc}")
    settings = load_settings()
    try:
        Repository(settings.db_path).ping()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"database unavailable: {exc}")
    if not EvidenceStore(settings.evidence_dir).is_writable():
        problems.append("evidence store is not writable")
    return problems


if __name__ == "__main__":
    port = os.environ.get("STREAMLIT_SERVER_PORT", "8501")
    issues = check(f"http://127.0.0.1:{port}/_stcore/health")
    print("ready" if not issues else "not ready: " + "; ".join(issues))
    sys.exit(1 if issues else 0)
