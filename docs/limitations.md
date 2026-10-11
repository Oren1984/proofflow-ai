# Limitations

ProofFlow AI is a proof of concept. This page lists what it does not do and what its results do not establish.

## Scope

- Built for one user on one machine with a handful of small projects. It has not been load-tested and SQLite is used without any concurrency design beyond short transactions.
- Runs locally. It is not deployed anywhere and has no hosting, backup or upgrade story.
- The demonstration data is synthetic. It has not been evaluated with real projects or real teams, so no time-saving or quality-improvement figures are claimed.

## No enterprise authentication

- There is no login. Anyone who can reach the port can read and change everything.
- Reviewer and submitter names are free-text labels typed into the sidebar. They are not verified identities, and one person can act under any name.
- There are no roles: the same user can submit evidence and approve it.
- Docker Compose publishes the port on `127.0.0.1` only. **Do not expose the app to a network or the internet** without adding authentication and a security review.

## Evidence is not independently verified

- **Links are never opened.** A URL is recorded as an unverified reference and can never satisfy a criterion.
- **Test results are self-reported.** A JSON record is checked for structure and consistency, not for origin. The tool cannot tell whether a real test run produced it.
- **Screenshots and documents are not interpreted.** The tool confirms a file is a real PNG, JPEG or PDF and has not changed. It does not read what the image shows or what the document says. That judgement is the human reviewer's.
- **Type checks are shallow.** Files are identified by signature and basic structure only. There is no malware scanning and no deep parsing of images or PDFs.
- **Freshness trusts the timestamps.** Evidence age comes from the upload time or the `executed_at` value in a test record, both of which the submitter controls.

## No guarantee that evidence is truthful

A SHA-256 hash shows that a stored file is identical to what was uploaded. It does not show that the content is accurate, current, taken from the right environment, or honestly produced. A `VERIFIED` status means: the evidence passed the configured automated checks and a person recorded an approval under a label. Nothing more.

## Not a legal e-signature

Approvals, rejections and recorded customer acceptance are application records. They carry no cryptographic signature, no identity proof and no legal weight, and should not be presented as signatures or as contractual acceptance.

## No automatic customer acceptance

Customer acceptance is whatever a user enters. The tool does not contact the customer, does not authenticate them, and cannot confirm that acceptance actually happened. It never infers acceptance from readiness, and it clears recorded acceptance when the delivery changes.

## History is not a tamper-proof audit trail

The review history is an append-only SQLite table protected by triggers inside the application's own database. Anyone with file access can edit or replace the database. Deleting a project deletes its history. Treat it as local traceability, not as evidence that would stand up in an audit or dispute.

## No compliance certification

The project makes no claim of conformity with GDPR, ISO 27001, SOC 2, eIDAS or any other regulation or standard. Data is stored unencrypted on local disk. Retention is manual: data remains until a user deletes the evidence item, the project, or the Docker volume.

## Optional LLM

- **Not tested against the live API.** The adapter is covered by tests with a stubbed client and was checked against the interface of `anthropic` 1.13.0, but no real request was made during development. Expect to validate it before relying on it.
- Not installed by default. The Docker image includes the client only when built with `INSTALL_LLM=true`; without it, LLM mode falls back to Mock and says so.
- When enabled, criterion text, statuses, evidence IDs and evidence descriptions are sent to Anthropic's API. Evidence file contents are not.
- Server-side refusal fallbacks are not configured; a refusal simply falls back to Mock.
- Output is advisory wording only. It is validated for shape and for citing only known IDs, not for factual accuracy, and should be read before use.
- Prompt-injection resistance rests on keeping untrusted text in the data channel and on the assistant having no ability to act. The model's adherence to its instructions is not otherwise tested.

## Functional gaps

- No PDF export of the report; it is HTML.
- No in-app preview for PDF evidence.
- One evidence item belongs to exactly one criterion; the same file must be uploaded again to support another.
- Readiness is unweighted; every criterion counts the same.
- A single invalid evidence item blocks its criterion until it is removed, even when other valid evidence exists.
- No user-facing notification, reminders, or integration with task trackers, CI systems or storage services.
- No database migrations; a schema change would require recreating local data.
- The UI is English-only and designed for desktop widths.
