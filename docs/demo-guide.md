# Demo guide

A ten-minute Before/After walkthrough using the bundled synthetic scenario. Everything in it is fictional.

## Scenario

**Customer Analytics Dashboard** for *Northwind Retail (synthetic)*. A small team has delivered a reporting dashboard and marked all five commitments as complete.

| Commitment | Acceptance criterion | Evidence policy |
|---|---|---|
| Dashboard Delivery | Dashboard is accessible at the agreed URL | 1 screenshot |
| | Required KPIs (revenue, active customers, churn) are displayed | 1 screenshot |
| Data Export | CSV export produces the expected fields | Test result or text; passing test required |
| Quality Validation | Required automated tests pass | Test result; passing test required; max 30 days old |
| Documentation | User guide is delivered as a PDF | 1 PDF |
| | Release notes are delivered | 1 text or PDF |
| Customer Handover | Final stakeholder review is recorded | PDF, text or link |

All seven criteria require human review.

## Start

```bash
docker compose up --build -d
```

Open <http://localhost:8501>. The project is seeded in its *Before* state on first start. To start over at any time: sidebar → **Demo scenario** → **Reset demo to 'Before' state**.

## Part 1 — Before: reported complete, not verified

**1. Overview.** Expected:

- Banner: `INCOMPLETE - not verified (0 of 7 acceptance criteria verified)`
- Verified criteria `0 / 7`, missing or invalid evidence `6`, pending human review `1`, readiness `0%`
- "5 of 5 commitments are reported complete. 0 of 5 are verified by evidence and human review."
- Internal completion `NOT_READY`, customer acceptance `NOT_RECORDED`

**2. Verification & Approval → Run verification.** Expected findings:

| Criterion | Status | Why |
|---|---|---|
| Dashboard is accessible | `PENDING_HUMAN_REVIEW` | A valid screenshot exists, but a screenshot alone proves nothing until a person confirms it |
| Required KPIs are displayed | `MISSING_EVIDENCE` | Nothing attached |
| CSV export produces the expected fields | `INSUFFICIENT_EVIDENCE` | Only a note called `export_tests_passed.txt`; a filename is not a test run |
| Required automated tests pass | `INSUFFICIENT_EVIDENCE` | The test record reports 3 failed of 42 |
| User guide is delivered as a PDF | `INVALID_EVIDENCE` | A draft Markdown file where a PDF is required |
| Release notes are delivered | `MISSING_EVIDENCE` | Nothing attached |
| Final stakeholder review is recorded | `INSUFFICIENT_EVIDENCE` | Only a link, which is an unverified reference |

Try **Draft follow-up message** on any open criterion. The draft is labelled as simulated (Mock) assistance and changes nothing.

Note that **Approve** is offered only for the first criterion. The others cannot be approved until their automated findings are resolved.

**3. Delivery Package → Generate delivery package** (optional). The file name ends in `_INCOMPLETE.zip`, and the report opens with the incomplete banner and lists all seven outstanding items.

## Part 2 — After: evidence-based delivery

**4. Resolve one gap by hand.** Evidence Workspace → select *Dashboard Delivery - Required KPIs…* → upload `demo/synthetic_data/dashboard_kpis.png`, describe it, **Attach evidence**. The warning drops from 2 criteria without evidence to 1, and the item shows `VALID` with its SHA-256 prefix.

**5. Supply the rest.** Sidebar → **Demo scenario** → **Apply 'After' evidence**. This uses the same service calls as manual upload to:

- attach a passing CSV-export test record,
- attach a passing regression re-run (the earlier failed run stays on file; the latest run governs),
- remove the draft guide and attach `user_guide.pdf`,
- attach `release_notes.md`,
- attach `stakeholder_review_minutes.pdf`.

Test records are generated at that moment so they are never stale. The helper does **not** approve anything.

**6. Verification & Approval → Run verification.** Expected: all seven criteria are `PENDING_HUMAN_REVIEW`. Readiness is still `0%` — evidence passing automated checks is not the same as verified.

**7. Human review.** Set your label in the sidebar (**Your name or role**), inspect each criterion's evidence, and click **Approve** on each. To see the alternative path, reject one with a reason, then revoke the rejection.

**8. Overview.** Expected:

- Banner: `Internally verified - customer acceptance NOT recorded`
- Verified criteria `7 / 7`, readiness `100%`
- Internal completion `INTERNALLY_VERIFIED`, customer acceptance still `NOT_RECORDED`

**9. Delivery Package.**

- **Generate delivery package** → **Download ZIP**. The file name no longer says `INCOMPLETE`.
- Enter how the customer responded and click **Record customer acceptance**. The banner changes to `Internally verified - customer acceptance recorded`. Generate again to get a package that includes the acceptance record.

## Inspecting the package

Unzip the download:

| File | Contents |
|---|---|
| `report.html` | Open in a browser. Status banner, summary, exceptions and outstanding items, every commitment and criterion with its evidence and hashes, the full review history, and a statement of what the report does and does not establish |
| `manifest.json` | The same information as data: package metadata, completion state, customer acceptance, criteria with status and explanation, each evidence item with SHA-256, validity and its path in the archive, outstanding items, review history |
| `evidence/` | The evidence files, named by evidence ID |

To check integrity yourself, compare `sha256sum evidence/<file>` with the `sha256` value for that item in `manifest.json`. The report footer shows the SHA-256 of `manifest.json` itself.

## Part 3 — Stale approvals (optional)

With everything verified and accepted, go to Evidence Workspace and **Remove** any evidence item. Expected immediately:

- that criterion is no longer `VERIFIED` and its approval is cleared (`MISSING_EVIDENCE` if that was its only evidence, otherwise back to `PENDING_HUMAN_REVIEW`),
- customer acceptance returns to `NOT_RECORDED`,
- the review history shows `EVIDENCE_REMOVED`, `DECISION_INVALIDATED` and `CUSTOMER_ACCEPTANCE_CLEARED`.

## What the demo shows

| Measure | Before | After review |
|---|---|---|
| Gaps detected without manual checking | 6 of 7 criteria | 0 |
| Verification coverage (criteria verified) | 0 of 7 | 7 of 7 |
| Outstanding items visible in the package | 7, each with a reason and next action | 0 |
| Evidence files in the package with hashes | 4 | 9 |
| Review history entries in the package | 5 | 19 (plus one per earlier export) |

These are counts produced by the application for this scenario. Processing time on the development machine was about 13 ms to assess the project and about 45 ms to build the package. The time a real team would save preparing a handover has not been measured and is not claimed.
