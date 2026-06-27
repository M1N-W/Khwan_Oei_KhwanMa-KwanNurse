# ขวัญเอ๋ยขวัญมา LINEBot - Master Roadmap

## 1. Document Purpose

This document is the operational source of truth for implementation order, dependencies, status, acceptance criteria, and commit tracking for ขวัญเอ๋ยขวัญมา LINEBot work. `PRODUCT_VISION.md` remains the product vision, `ARCHITECTURE.md` remains the current architecture overview, and `SPRINT_2_PLAN.md` is historical sprint planning material.

## 2. Current Baseline

Branch: `codex/dashboard-command-center`

Application baseline commit:
9cd5e7ae958783fd06fe92a84fa45aa47ecf1f30

Roadmap commit:
096097128aae008dd13796dd15be7435edb57c66

Current full test baseline:
447 unittest tests passing

Current full test baseline: 447 unittest tests passing.

Current run artifact: .chatgpt/codex-runs/2026-06-22T000000Z-roadmap-sync-kwn01-failed-alert-visibility/RESULT.md

Known allowed dirty local artifacts:

- `skills-lock.json`
- `.chatgpt/codex-runs/2026-06-20T000000Z-canonical-risk-level/RESULT.md`
- `.chatgpt/codex-runs/2026-06-20T125810Z-symptom-assessment-reliability-contract/RESULT.md`

No secret values, real patient identifiers, tokens, or environment configuration are recorded here.

## 3. Completed Foundation Work

### KWN-00A - Canonical Risk-Level Contract

Status: `done`

Commit: `f66f007e651331d7c50736cca6afde3f5040cff3`

This normalized risk levels across symptom scoring and dashboard display so clinical severity is interpreted consistently. It also preserved legacy Thai and emoji risk values through canonical conversion.

### KWN-00B - Symptom Assessment Reliability Contract

Status: `done`

Commit: `76430849d5616bc9d105c31a24ba624edde7bf82`

This made symptom-assessment persistence and nurse notification failures explicit without losing the patient-facing response. Failed high-risk nurse deliveries are now preserved in `FailedNurseAlerts` with an idempotency key for future recovery workflows.

## 4. Architecture Decisions

| ID      | Decision                                                                                                              |
| ------- | --------------------------------------------------------------------------------------------------------------------- |
| ADR-001 | LINE User ID remains the immutable internal routing and join key.                                                     |
| ADR-002 | Name, surname, HN, and phone are clinical identity fields used by nurses.                                             |
| ADR-003 | Self-entered identity is registered, not verified.                                                                    |
| ADR-004 | Patient registration uses a soft gate and never blocks urgent clinical workflows.                                     |
| ADR-005 | `PatientProfile` is the identity source of truth.                                                                     |
| ADR-006 | Clinical reminders and surveys must use persistent due records; in-memory scheduler jobs are not the source of truth. |
| ADR-007 | Quick Reply accelerates UX but every flow must have a text fallback.                                                  |
| ADR-008 | Flex Messages summarize or confirm information; they do not replace every data-entry step.                            |
| ADR-009 | Survey tracking links use opaque tokens and contain no PII.                                                           |
| ADR-010 | Clinical alerts and survey/engagement work remain separate dashboard concepts.                                        |
| ADR-011 | New high-impact features should be protected by feature flags.                                                        |
| ADR-012 | One bounded reviewed work unit maps to one local commit.                                                              |

## 5. Ordered Work Units

| Work Unit                                                     | Status    |
| ------------------------------------------------------------- | --------- |
| KWN-01 - Failed Nurse Alert Read-only Visibility              | `done`    |
| KWN-02 - Patient Registry Contract                            | `done`    |
| KWN-03 - Failed Alert Manual Recovery                         | `done`    |
| KWN-04 - Persistent Due Dispatcher                            | `done`    |
| KWN-05 - LINE Message Delivery Layer                          | `done`    |
| KWN-06 - Registration Quick Reply and Flex UX                 | `done`    |
| KWN-07 - Engagement Tracking and Survey Scheduling            | `done`    |
| KWN-08 - Survey Completion and Dashboard Analytics            | `done`    |
| KWN-09 - Unified Clinical Alert and Incremental Webhook Split | `done`    |

## 6. Dependency Graph

```text
KWN-01 -> KWN-03

KWN-02 -> KWN-05 -> KWN-06 -> KWN-07 -> KWN-08

KWN-04 -> KWN-07

KWN-03 + KWN-08 -> KWN-09
```

## 7. Work-Unit Definitions

### KWN-01 - Failed Nurse Alert Read-only Visibility

Status: `done`

Commit: `9cd5e7ae958783fd06fe92a84fa45aa47ecf1f30`

Purpose: expose pending or failed nurse-notification deliveries that are already persisted in `FailedNurseAlerts`.

Major scope: read-only data access, dashboard snapshot, dedicated authenticated page, home metric, bell breakdown, empty and degraded states.

Explicit exclusions: retry, resend, acknowledgement, status mutation, scheduler work, Patient Registry, phone, survey, Quick Reply, Flex, LIFF, HIS.

Dependencies: KWN-00B.

Expected value: nurses can see operational delivery failures instead of relying on hidden backlog rows.

Main risks: PHI leakage through raw payloads or notification text, confusing delivery failures with clinical alerts, accidentally creating write paths.

Exit criteria: authenticated nurses can see pending/failed records, malformed rows do not break the page, empty and degraded states differ, no raw payload/message renders, no write operation is introduced, full tests pass.

### KWN-02 - Patient Registry Contract

Status: `done`

Purpose: turn the existing partial patient identity fields into a registration contract suitable for clinical follow-up.

Major scope: add phone, registration status, consent fields, registered and last-active timestamps, dashboard display/edit support, soft gate rules.

Explicit exclusions: Flex UI, Quick Reply flows, survey scheduling, HIS validation, automatic account merging.

Dependencies: none beyond existing `PatientProfile`.

Expected value: nurses can identify and contact patients using clinical fields rather than LINE IDs alone.

Main risks: treating self-entered data as verified, blocking urgent flows, exposing phone numbers too broadly.

Exit criteria: additive schema works with old rows, registered/incomplete status is deterministic, phone display is privacy-safe, urgent workflows remain available.

### KWN-03 - Failed Alert Manual Recovery

Status: `done`

Purpose: let authorized nurses manually retry or resolve failed delivery rows after KWN-01 proves visibility.

Major scope: server-side retry action, row re-read, idempotency-key reuse, status transitions, retry count, audit fields, double-submit protection.

Explicit exclusions: automatic retry worker and broad alert lifecycle rewrite.

Dependencies: KWN-01.

Expected value: failed clinical notifications can be closed by an operator instead of remaining a passive backlog.

Main risks: duplicate LINE delivery, concurrent sends, stale patient state.

Exit criteria: retry is authenticated and CSRF-protected, only actionable rows can be retried, state changes are audited, duplicate concurrent attempts are blocked.

### KWN-04 - Persistent Due Dispatcher

Status: `done`

Commit: `52de418`, `cd77ca7`

Purpose: replace long-lived in-memory jobs as the source of truth for due reminders and future surveys.

Major scope: recurring dispatcher loop, persistent due rows, claim/send/update lifecycle, bounded retries, catch-up after restart.

Explicit exclusions: survey-specific analytics and UI.

Dependencies: existing reminder scheduling.

Expected value: scheduled work survives deploys and restarts.

Main risks: duplicate sends, unbounded retries, contention if worker count changes.

Exit criteria: due rows are claimed once, overdue rows are caught up, send results persist, metrics separate success/failure.

### KWN-05 - LINE Message Delivery Layer

Status: `done`

Commit: `94ed28a`

Purpose: create a safe abstraction for sending text, quick replies, and Flex messages.

Major scope: message builders, reply/push helpers for LINE message objects, payload validation, fallback text, feature flags.

Explicit exclusions: replacing all flows at once.

Dependencies: KWN-02 for registration data shape.

Expected value: rich LINE UX can be added without ad hoc payload construction in webhook handlers.

Main risks: sending duplicate replies, exceeding LINE limits, breaking Dialogflow text paths.

Exit criteria: existing text behavior remains stable, rich payloads are unit-tested, unsupported modes fall back to text.

### KWN-06 - Registration Quick Reply and Flex UX

Status: `done`

Commit: `191896c`

Purpose: make patient registration easier while keeping text fallback.

Major scope: registration flow, quick reply accelerators, profile summary Flex, edit/confirm actions, resume behavior.

Explicit exclusions: survey dispatch and HIS validation.

Dependencies: KWN-02 and KWN-05.

Expected value: patients can complete identity details with less typing and clearer confirmation.

Main risks: confusing registered with verified, making Flex too dense for mobile, losing free-text fallback.

Exit criteria: flow can be completed or resumed, Flex uses privacy-safe fields, fallback text works without rich message support.

### KWN-07 - Engagement Tracking and Survey Scheduling

Status: `done`

Commit: `73705a7`

Purpose: schedule satisfaction surveys after real bot use milestones.

Major scope: survey schedule rows, opaque tracking tokens, redirect endpoint, sent/clicked/failed tracking, milestone 7/14/21/30 days.

Explicit exclusions: Google Form completion ingestion and analytics beyond delivery/click status.

Dependencies: KWN-02, KWN-04, KWN-05, and KWN-06.

Expected value: survey delivery becomes measurable without exposing patient identifiers in links.

Main risks: direct Google Form links without tracking, PII in URLs, missed sends after restart.

Exit criteria: due surveys are sent through persistent records, tokens are opaque, clicks are recorded, failures are visible.

### KWN-08 - Survey Completion and Dashboard Analytics

Status: `done`

Commit: `fc0876e`

Purpose: connect submitted survey responses to the schedule lifecycle and make results visible.

Major scope: completion matching by survey code or callback, response and completion rates, patient survey timeline, overdue filters.

Explicit exclusions: AI sentiment analysis.

Dependencies: KWN-07.

Expected value: the team can distinguish sent, clicked, and completed surveys.

Main risks: mismatched responses, exposing raw feedback too broadly, conflating survey overdue with clinical alerts.

Exit criteria: completion state is accurate, dashboard separates engagement from clinical urgency, analytics avoid unnecessary PHI.

### KWN-09 - Unified Clinical Alert and Incremental Webhook Split

Status: `done`

Commit: `84ee985`, `bc15843`, `2c5c72e`, `92d81ee`, `b43743f`

Purpose: consolidate proven alert and engagement concepts after visibility, manual recovery, and survey analytics are stable.

Major scope: clinical alert lifecycle, assignment/acknowledgement/resolution, incremental webhook modularization, shared action contracts.

Explicit exclusions: full webhook rewrite and microservices.

Dependencies: KWN-03 and KWN-08.

Expected value: clinical operations become easier to reason about without a disruptive rewrite.

Main risks: large refactor blast radius, mixing engagement and clinical concepts, breaking existing Dialogflow flows.

Exit criteria: new modules preserve behavior, tests cover current contracts, rollout can be reversed by commit.

## 8. Delivery Workflow

1. Verify HEAD and worktree.
2. Inspect relevant files only.
3. Add focused tests.
4. Implement the smallest coherent patch.
5. Run targeted tests.
6. Run full unittest suite.
7. Run `compileall`.
8. Run `git diff --check`.
9. Review actual diff.
10. Commit approved paths only.
11. Do not push until release review.

## 9. Status Values

Use only: `planned`, `active`, `review`, `done`, `blocked`, `deferred`.

## 10. Deferred Work

- Dialogflow CX migration
- Full LIFF application
- HIS integration
- Database migration away from Google Sheets
- Microservices
- Full webhook rewrite
- AI sentiment analysis for survey feedback
- Automatic failed-alert retry before manual recovery workflow is proven
