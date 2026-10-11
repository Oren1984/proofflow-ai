# Assurance

How this POC is tested, secured and observed, and where its governance boundaries are. Controls are sized for a local, single-user demonstration that handles synthetic data.

## QA approach

Tests were written alongside the code and run with `pytest`.

| Suite | Tests | Location | What it exercises |
|---|---|---|---|
| Unit | 124 | `tests/unit` | Rules in isolation, input validation, AI adapters, report rendering, UI text escaping |
| Integration | 66 | `tests/integration` | Services against a real SQLite database and file store in a temp directory |
| Browser E2E | 2 | `tests/e2e` | The real Streamlit app driven through Chromium with Playwright |

**Result at delivery: 192 passed, 0 failed, 0 skipped.** The E2E suite was also run against the Docker container (`PROOFFLOW_E2E_URL=http://localhost:8501 pytest tests/e2e`) and passed.

The main E2E test follows the user journey: open the app → inspect the unverified demo project → run verification → read the findings → upload a missing screenshot through the UI → apply the remaining evidence → re-run verification → approve seven criteria one by one → generate and download the package → open the ZIP and check the manifest, hashes and report → record customer acceptance. A second test removes approved evidence and confirms verification and acceptance are withdrawn.

Negative and security cases covered include path-traversal filenames, unsupported and disguised file types, oversized and empty uploads, malformed test records, missing and corrupted files, invalid review transitions, replay of a stale approval, HTML injection into the exported report, invalid LLM responses, LLM timeout, and fallback to Mock.

One defect was found by the tests during development and fixed: verifying or exporting a non-existent project raised a raw database error instead of a clear validation error.

## Security controls

**Uploads**

- Extension allowlist: `.png .jpg .jpeg .pdf .txt .md .json`. Everything else is refused.
- Size limit of 5 MB by default, enforced by Streamlit and again in the service.
- Content must match the extension (image signatures, PDF header and trailer, UTF-8 text, parseable JSON).
- Files are stored under generated names (`<project id>/<evidence id><ext>`). The client filename is sanitized and kept only as metadata; it is never used as a path.
- The file store resolves every path and refuses anything outside its root.
- Uploaded content is never executed, imported, deserialized into objects, or passed to a shell. JSON is parsed with the standard library and validated field by field.
- Evidence is stored outside any served directory.

**Application**

- All SQL is parameterized. Table and column names come from code, never from input.
- Free-text input is length-limited and stripped of control characters.
- The HTML report escapes every dynamic value, contains no links or scripts, and carries a restrictive Content-Security-Policy.
- In the UI, user-controlled text is escaped before it reaches Markdown, so it cannot inject links, images or formatting.
- ZIP entry names are built only from generated IDs and allowlisted extensions.
- Link evidence must be `http` or `https` and is never fetched.
- Duplicate evidence for a criterion and duplicate project names are rejected.
- Secrets come from environment variables only; `.env` is git-ignored and the API key is excluded from the settings representation, prompts and logs.

**Container**

- Runs as an unprivileged user (uid 10001) with all Linux capabilities dropped and `no-new-privileges`.
- The port is published on `127.0.0.1` only.

**Not provided:** authentication, authorization, encryption at rest, malware scanning, rate limiting. See [limitations](limitations.md).

## Observability

Structured JSON logs go to stdout (`docker logs proofflow-ai`). Events:

`project.created` · `commitment.created` · `criterion.created` · `evidence.registered` · `evidence.upload_rejected` · `evidence.removed` · `verification.completed` · `evidence.validation_failed` · `review.approved` · `review.rejected` · `review.revoked` · `review.decision_invalidated` · `review.commitment_reopened` · `acceptance.recorded` · `acceptance.cleared` · `export.generated` · `export.failed` · `project.deleted` · `demo.seeded` · `demo.after_applied` · `ai.fallback_to_mock` · `ui.unexpected_error`

Entries carry project, criterion and evidence IDs and an operation ID for verification and export. They never contain evidence content, descriptions, prompts or keys.

`verification.completed` reports duration, criteria total and verified, missing-evidence count, validation-failure count and readiness. Run metrics are also stored in the `verification_runs` table. The sidebar **Diagnostics** panel shows database and file-store status, number of runs, last run duration, and export success and failure counts.

**Health.** The Docker health check runs `python -m app.healthcheck`, which confirms that Streamlit's `/_stcore/health` endpoint responds, the database opens, and the evidence store is writable.

There is deliberately no metrics server, tracing or log aggregation.

## Auditability

Significant actions are appended to the `audit_log` table with action, entity, previous and new state, timestamp, actor label and reason: evidence added and removed, approvals, rejections, revocations, system invalidations, commitment reopening, customer acceptance recorded and cleared, and exports generated or failed. The history is shown per criterion in the UI and included in full in every delivery package.

Database triggers block updates to history rows and block deletion while the project exists.

This is a **local traceability mechanism**. Anyone with access to the SQLite file can alter it, and actor names are unverified labels. It is not an immutable or compliance-grade audit ledger.

## AI guardrails

- **Mock is the default** and needs no network. It is labelled in the UI as simulated assistance.
- **The assistant cannot act.** It receives a dictionary and returns text. It has no access to the repository or services, and authoritative modules do not import it.
- **Bounded input.** Free-text fields are truncated to 300 characters and lists to 40 items. Evidence file contents are never sent.
- **Trust boundary.** User-supplied text travels only inside the JSON data of the user message; the system prompt tells the model to treat it as data and never to contradict the authoritative status fields.
- **Strict output.** The response must be JSON with exactly `text` and `evidence_refs`; the text must be non-empty and at most 2,000 characters; every cited ID must exist in the input. Anything else is discarded.
- **Timeout and fallback.** One attempt with a configurable timeout (default 20 s). Any error, timeout, refusal or invalid response falls back to the Mock, and the UI says so.
- **Always labelled** as a drafting suggestion that does not change any status.

The LLM path is tested with a stubbed client and checked against the installed SDK's interface. It has not been run against the live API.

## Governance boundaries

- Synthetic data only. The demo names a fictional customer and contains no personal data.
- No claim of GDPR, ISO 27001, SOC 2 or any other compliance or certification.
- A human approval here is a recorded opinion under a typed label, not an electronic signature.
- Recorded customer acceptance reflects what a user entered; the tool does not contact or authenticate the customer.
- Enabling LLM mode sends criterion text, statuses, and evidence IDs and descriptions to Anthropic's API. That is a decision for whoever configures it.
