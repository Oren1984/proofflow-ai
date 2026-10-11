# Verification model

All of this is implemented in [`app/rules.py`](../app/rules.py) (rules version `1.0`) and [`app/services/approval_service.py`](../app/services/approval_service.py).

## Evidence policy

Each acceptance criterion defines:

| Setting | Meaning | Default |
|---|---|---|
| Accepted evidence types | Screenshot, PDF document, text/Markdown, structured test result, external link | — |
| Minimum valid evidence items | How many valid items are needed | 1 |
| Requires a passing test result | A structured test record must show a passing run | off |
| Maximum evidence age (days) | Older evidence is stale | no limit |
| Requires human review | A reviewer must approve | on |

A policy that accepts only links is rejected, because links can never satisfy a criterion.

## Evidence validation

Each evidence item is checked against the criterion it is linked to and ends up in one of four states.

| Result | When |
|---|---|
| `INVALID` | Linked to a different commitment or criterion; type not accepted by the criterion; stored file missing; file larger than the limit; SHA-256 does not match the recorded hash; content does not match the file type; test-result record malformed |
| `STALE` | Otherwise valid, but older than the criterion's maximum age. Age is measured from the test's `executed_at` for test results and from the upload time for everything else |
| `UNVERIFIED_REFERENCE` | An external link. Recorded, never fetched, never counted |
| `VALID` | Passed every check above |

Content-type checks: PNG and JPEG signatures; PDF header and end-of-file marker; UTF-8 text with no NUL bytes; JSON that parses to an object.

### Structured test results

A test result is a JSON file:

```json
{"suite": "regression", "total": 42, "passed": 42, "failed": 0, "skipped": 0, "executed_at": "2026-10-10T09:30:00Z"}
```

It is valid when the counts are non-negative integers, `passed + failed + skipped = total`, `total > 0`, and `executed_at` is a parseable timestamp that is not in the future. A valid record is **passing** when `failed = 0` and `passed > 0`.

Only this record counts as test execution. A file called `tests_passed.txt` or a description saying "all tests pass" is ordinary text evidence.

## Criterion status — precedence

The first matching rule wins.

| # | Status | Condition |
|---|---|---|
| 1 | `MISSING_EVIDENCE` | No evidence is linked |
| 2 | `INVALID_EVIDENCE` | Any linked item is `INVALID` |
| 3 | `INSUFFICIENT_EVIDENCE` | Fewer `VALID` items than the minimum; **or** the most recent valid test result is failing; **or** a passing test is required and no valid test result exists |
| 4 | `REJECTED` | A current human rejection stands |
| 5 | `PENDING_HUMAN_REVIEW` | Human review is required and there is no current approval |
| 6 | `VERIFIED` | None of the above |

Consequences worth stating explicitly:

- **One invalid item blocks the criterion** even if other items are valid. It must be removed or replaced, so a package never ships with known-bad evidence attached.
- **The latest test run governs.** A later passing run supersedes an earlier failure, and a later failure overrides an earlier pass. A failing latest run blocks the criterion even where a test is not strictly required, because it contradicts the claim.
- **Automated findings outrank human decisions.** An approval cannot turn rules 1–3 into `VERIFIED`. File integrity checks and human approval are separate and neither substitutes for the other.
- **A screenshot is never proof on its own.** A valid screenshot leads to `PENDING_HUMAN_REVIEW`; a person decides whether it demonstrates the criterion.

Every finding includes an explanation, the specific unmet requirements, and a next action.

## Human review

| Action | Allowed when | Requires |
|---|---|---|
| Approve | Status is `PENDING_HUMAN_REVIEW` | Reviewer label |
| Reject | Status is `PENDING_HUMAN_REVIEW` or `VERIFIED` | Reviewer label, reason |
| Revoke | A decision exists | Reviewer label, reason |
| Reopen commitment | Any time | Reviewer label, reason — clears every decision under the commitment and sets its reported status to `IN_PROGRESS` |

Anything else is refused with an explanation, for example approving a criterion with missing evidence, or revoking when there is no decision.

## State invalidation

A human decision is valid only for exactly what the reviewer saw.

| Change | Effect |
|---|---|
| Evidence added to or removed from a criterion | That criterion's decision is cleared (`DECISION_INVALIDATED`) |
| Criterion text or policy edited | That criterion's decision is cleared |
| Any of the above, a rejection, a revocation, a reopened commitment, a criterion added or deleted, or a commitment deleted | Recorded customer acceptance is cleared (`CUSTOMER_ACCEPTANCE_CLEARED`) |
| Stored file modified or deleted outside the app | The item becomes `INVALID`, so the criterion becomes `INVALID_EVIDENCE` regardless of any approval |

As a safeguard, each decision also stores a fingerprint (SHA-256 over the criterion text, policy, and the IDs and hashes of its evidence). A decision whose fingerprint does not match the current state is ignored even if it is still present in the database.

## Readiness and completion

```text
Readiness = verified criteria / total criteria × 100
```

Every criterion is required and counts equally; there are no weights. A project with no criteria has 0% readiness and the state `NO_CRITERIA`.

| Internal completion state | Meaning |
|---|---|
| `NO_CRITERIA` | Nothing to verify |
| `NOT_READY` | At least one criterion is missing, invalid, insufficient or rejected |
| `AWAITING_REVIEW` | Every criterion is verified or pending human review |
| `INTERNALLY_VERIFIED` | Every criterion is verified |

**Customer acceptance** is a separate field: `NOT_RECORDED`, `ACCEPTED` or `DECLINED`. It changes only when a person records it. Acceptance can be recorded only for an `INTERNALLY_VERIFIED` delivery; a decline can be recorded at any time and needs a note. 100% readiness never sets it.

## Reproducibility

The rules take recorded inputs and a timestamp and nothing else. The same evidence records, stored bytes, policy, decisions and time produce the same findings. Each recorded run stores its time, duration, rules version and result counts.

## What integrity evidence does and does not show

A matching SHA-256 hash shows that a file is byte-for-byte what was uploaded. It says nothing about whether the content is accurate, whether a screenshot was taken on the right environment, or whether a test record was produced by a real test run. Those judgements belong to the human reviewer, which is why review is on by default.
