# KWN Implementation Roadmap

## 1. Purpose

This document explains the ขวัญเอ๋ยขวัญมา LINEBot implementation plan from the current state through production readiness.

It is written for AI coding agents and human reviewers who need to continue the project without re-deriving the architecture from chat history.

Authoritative references:

- `docs/MASTER_ROADMAP.md` is the operational source of truth for current work-unit numbers, status, dependencies, and commit tracking.
- `PRODUCT_VISION.md` is the product vision and long-term care-platform direction.
- `ARCHITECTURE.md` describes the current Flask, LINE, Dashboard, and Google Sheets architecture.
- `SPRINT_2_PLAN.md` is historical planning material and must not override the active roadmap.

Important numbering decision:

- The current `docs/MASTER_ROADMAP.md` already defines KWN-03 as Failed Alert Manual Recovery.
- Do not renumber KWN-03 to Clinical Workflow.
- Clinical Workflow should be introduced after the currently approved operational sequence, or as a later roadmap expansion after KWN-09, unless `MASTER_ROADMAP.md` is intentionally revised in a separate roadmap-sync change.

## 2. Product Vision

ขวัญเอ๋ยขวัญมา LINEBot is evolving from a LINE chatbot into a clinical digital assistant platform for post-surgical tele-nursing.

The platform should support:

- patient identity and registration
- symptom assessment and risk stratification
- nurse alerting and failed-alert recovery
- personalized education
- follow-up and survey engagement
- teleconsult workflow
- nurse dashboard operations
- analytics
- reliability, privacy, auditability, and production readiness

The core care loop is:

```text
Patient on LINE
  -> identity and registration
  -> clinical assessment
  -> risk classification
  -> recommendation or escalation
  -> follow-up or survey schedule
  -> nurse dashboard visibility
  -> analytics and operational review
```

## 3. Current Repository Baseline

Repository:

```text
Repo ID: kwan-nurse-linebot
Display name: ขวัญเอ๋ยขวัญมา LineBot
Root: C:\Kwan_LineBot\Linebot-Code\kwannurse-linebot
Branch: codex/dashboard-command-center
Expected current HEAD for KWN-02 rework: d55e23471f3ea026040d86e2b2f0f5c6d511064a
```

Known current state:

- KWN-01 is implemented and committed.
- KWN-02 is partially implemented in the dirty worktree and remains under rework after review; it is not accepted or commit-ready.
- KWN-02 must not be committed until its privacy, last-active, storage outage, gate, dashboard-boundary, and regression-test requirements pass.
- `docs/MASTER_ROADMAP.md` should remain the committed source of truth; transient KWN-02 dirty-worktree status should be promoted there only after KWN-02 passes verification.
- `skills-lock.json` is a pre-existing dirty artifact and must be excluded from commits unless the user explicitly approves it.
- Existing `.chatgpt/codex-runs/**/RESULT.md` files listed in the active prompt are excluded artifacts unless the current run explicitly owns one result file.

Current technical stack:

- Python 3.12
- Flask app factory in `app.py`
- LINE Messaging API and Dialogflow webhook in `routes/webhook.py`
- Google Sheets data layer in `database/`
- service orchestration in `services/`
- Nurse Dashboard with Flask blueprint, Jinja2, and HTMX in `routes/dashboard/`
- process-local TTL cache in `services/cache.py`
- in-process metrics in `services/metrics.py`
- unittest-based verification in `tests/`

## 4. Non-Negotiable Execution Rules

Every implementation run must start with:

```powershell
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --short -uall
```

Do not:

- stage files unless the user explicitly approves staging
- commit unless the user explicitly approves commit
- push unless the user explicitly approves push
- restore, clean, reset, checkout, or stash dirty files without explicit approval
- modify `skills-lock.json` unless the task is specifically about skills
- modify old `.chatgpt/codex-runs/**` result files outside the active run
- expand scope into later KWN work units

Verification baseline for development work:

```powershell
python -m unittest tests.test_patient_registration -q
python -m unittest tests.test_patient_registration tests.test_patient_identity tests.test_dashboard_actions tests.test_dashboard_readers tests.test_dashboard_polish tests.test_integration_e2e tests.test_personalized_education -q
python -m unittest discover -s tests -q
python -m compileall -q app.py config.py routes services database utils tests
git diff --check
git status --short -uall
```

Only run the targeted subsets relevant to a narrow docs-only change unless code behavior changed.

## 5. Cross-Cutting Contracts

### 5.1 Privacy Contract

Never log or render unnecessary personally identifiable information.

PII includes:

- full phone number
- first name and last name
- HN
- consent values
- raw Dialogflow params
- raw notification payloads
- raw Google Sheets rows where not needed

Allowed logging shape:

```text
Intent: RegisterPatient | User: U123***7890 | ParamKeys: ['consent', 'phone']
```

Forbidden logging shape:

```text
Params: {"first_name": "...", "hn": "...", "phone": "..."}
```

Dashboard display rule:

- patient detail edit form may show full canonical phone to authenticated nurses
- dashboard list views must show masked phone only or no phone
- queue, alert, and failed-alert lists must not show full phone
- consent metadata is read-only text
- no nurse form may create or override consent

### 5.2 Patient Identity Contract

LINE User ID remains the immutable internal routing key.

Clinical identity fields are:

- first name
- last name
- HN
- phone

Self-entered identity is registered, not verified.

Do not use words or UI states implying hospital verification unless a future HIS integration validates identity.

### 5.3 Consent Contract

Consent must come only from dedicated consent parameters:

- `consent`
- `patient_consent`
- `privacy_consent`
- `registration_consent`

Do not infer consent from `query_text`.

Accepted affirmative values:

- `ยินยอม`
- `ตกลง`
- `yes`
- `agree`
- `true`
- `1`

Rejected ambiguous values:

- `ok`
- `okay`

Nurses cannot grant consent from the dashboard.

### 5.4 Google Sheets Contract

Google Sheets remains the current persistence layer, but all high-impact features must be designed so future migration to a relational or queue-backed store remains possible.

Rules:

- schema changes must be additive
- legacy headers must be preserved
- unknown future columns must be preserved
- short rows must be padded safely
- readers must tolerate malformed rows
- write paths must not create unnecessary rows
- write amplification must be controlled

### 5.5 Urgent Clinical Flow Contract

Urgent patient safety workflows must not be blocked by registration gates.

Bypass flows include:

- symptom report
- free-text symptom
- contact nurse
- after-hours choice
- cancel consultation
- knowledge/help flows that do not require patient identity
- image/audio clinical flows
- health and metrics endpoints

## 6. Work Units

### KWN-00A - Canonical Risk-Level Contract

Status: done.

Purpose:

Normalize risk level representation across symptom scoring, alerting, and dashboard display.

Core contract:

- risk labels must map to canonical internal levels
- legacy Thai and emoji values must remain readable
- dashboard sorting and severity display must not depend on raw strings

Exit criteria:

- canonical risk conversion is unit-tested
- dashboard and notification paths use canonical levels
- no behavioral regression in symptom scoring

### KWN-00B - Symptom Assessment Reliability Contract

Status: done.

Purpose:

Make symptom assessment failure-aware without losing high-risk alerts.

Core contract:

- patient-facing response can succeed even when nurse notification fails
- failed high-risk nurse alerts are persisted to `FailedNurseAlerts`
- failed alert rows include idempotency keys for later recovery
- raw payloads remain bounded and parseable

Exit criteria:

- failed nurse alert persistence is tested
- payload integrity is preserved
- no duplicate or malformed JSON is introduced

### KWN-01 - Failed Nurse Alert Read-Only Visibility

Status: done.

Purpose:

Expose failed nurse notification backlog to authenticated nurses.

Scope:

- read-only `FailedNurseAlerts` reader
- dashboard pending/actionable count
- dedicated failed-alert list page
- empty/loading/error/degraded states
- priority ordering with critical before high and older first

Out of scope:

- retry
- resend
- resolve
- automatic worker
- scheduler
- Patient Registry
- survey
- Quick Reply
- Flex Message

Exit criteria:

- dashboard shows pending count
- malformed rows do not crash dashboard
- Sheets outage shows degraded state
- raw `Payload_JSON` and `Notification_Message` are not rendered
- no write operation is added

### KWN-02 - Patient Registry Contract

Status: done.

Purpose:

Turn partial patient identity fields into a deterministic, privacy-safe registration contract.

Scope:

- `PatientProfile` schema extension
- first name, last name, HN, phone
- registration status
- registered timestamp
- consent version and consent timestamp
- last-active timestamp
- storage availability distinction
- dashboard display and nurse phone edit
- soft registration gate
- regression tests

Out of scope:

- Quick Reply
- Flex Message
- survey scheduling
- HIS validation
- automatic account merging
- phone verification via OTP

Current rework blockers:

- KWN-02 targeted tests must pass
- full verification must pass
- `RESULT.md` must use `# CODEX_RESULT`
- no commit until privacy logging, last-active, storage outage, and dashboard boundary tests are green

Acceptance criteria:

- `PatientProfile` uses the complete 16-column canonical header
- old rows are migrated additively on write
- unknown future columns survive updates
- invalid phone does not overwrite valid stored phone
- `Registration_Status` is derived, not trusted from caller input
- `Registered_At` is created only on first incomplete-to-registered transition
- historical `Registered_At` remains if status later becomes incomplete
- consent cannot be forged by submitted status/timestamp/version fields
- storage unavailable returns storage-unavailable wording and does not upsert
- last-active uses a six-hour throttle and does not read Sheets on every turn
- registration turn performs at most one PatientProfile upsert
- image/audio flows do not perform last-active Sheet I/O
- gate defaults disabled
- gate blocks only approved identity-dependent nonurgent intents
- urgent workflows bypass gate
- dashboard never lets nurses grant consent
- full phone appears only in authenticated patient detail edit input

### KWN-03 - Failed Alert Manual Recovery

Status: done.

Purpose:

Allow authorized nurses to manually retry or resolve failed nurse alert delivery rows after KWN-01 visibility is proven.

Dependencies:

- KWN-01
- KWN-00B

Scope:

- authenticated server-side retry action
- CSRF protection
- row re-read immediately before retry
- only actionable `pending` or allowed failed states can be retried
- reuse original `Idempotency_Key`
- status transitions to `sent`, `failed`, or `resolved`
- retry count update
- audit fields such as `Last_Attempt_At`, `Resolved_At`, and `Resolved_By`
- double-submit and concurrent-send protection

Out of scope:

- automatic retry worker
- broad clinical alert lifecycle rewrite
- survey or engagement alerts
- Patient Registry UI

Main risks:

- duplicate LINE delivery after timeout
- stale patient state
- concurrent retry attempts from multiple nurses or instances
- accidental retry of resolved records

Implementation notes:

- Prefer a claim/lease or optimistic update pattern if Sheets can support it safely.
- If Sheets cannot guarantee atomicity, keep retry manual and conservative.
- Do not retry rows whose risk context is stale beyond a defined age until a nurse explicitly resolves or recreates the clinical action.

Acceptance criteria:

- retry action is authenticated and CSRF-protected
- row is re-read before sending
- retry is blocked for non-actionable states
- concurrent double submit does not create two active sends
- success/failure is persisted
- audit fields are written
- dashboard shows clear result state
- no automatic worker is introduced

Regression requirements:

- pending row retry success
- pending row retry failure
- already resolved row blocked
- missing row blocked
- malformed row blocked gracefully
- CSRF failure
- unauthorized user blocked
- double submit blocked
- idempotency key reused

### KWN-04 - Persistent Due Dispatcher

Status: planned.

Purpose:

Replace long-lived in-memory scheduling as the source of truth for due reminders, future surveys, and other delayed sends.

Dependencies:

- existing reminder scheduling
- KWN-02 for patient identity and last-active context

Scope:

- persistent due records
- recurring dispatcher loop
- claim/send/update lifecycle
- bounded retries
- backoff
- catch-up after deploy or restart
- metrics for due, claimed, sent, failed, skipped

Out of scope:

- survey analytics
- full queue service migration
- multi-service architecture

Main risks:

- duplicate sends
- missed sends after restart
- unbounded retry loops
- contention if deployment gains multiple workers

Design requirement:

The due row, not the in-memory scheduler job, is the source of truth.

Acceptance criteria:

- due rows survive restart
- dispatcher catches up overdue rows
- one due item has at most one active claim
- send outcome is persisted
- retry policy is bounded
- stale claims can be recovered safely

Regression requirements:

- due item claimed once
- already claimed item skipped
- stale claim recovered
- send success persists sent state
- send failure increments retry state
- max retry produces dead-letter state
- restart catch-up scenario

### KWN-05 - LINE Message Delivery Layer

Status: planned.

Purpose:

Create a safe abstraction for text, Quick Reply, and Flex message delivery so rich UX is not assembled ad hoc in webhook handlers.

Dependencies:

- KWN-02 for registration data shape
- KWN-04 for persistent due sends

Scope:

- message object builders
- text fallback
- Quick Reply payload validation
- Flex payload validation
- LINE reply/push helper wrapper
- feature flags for rich message rollout
- payload size and field limits

Out of scope:

- converting all intents at once
- survey scheduling
- LIFF app

Main risks:

- breaking existing Dialogflow text behavior
- exceeding LINE payload limits
- duplicate reply or push
- using Flex for data entry where plain quick replies are safer

Acceptance criteria:

- existing text responses remain stable
- rich messages have text fallback
- unsupported mode falls back to text
- invalid payloads fail closed in tests
- message builders are deterministic

Regression requirements:

- text-only path unchanged
- Quick Reply builder validates action count
- Flex builder validates required fields
- fallback path used when feature flag off
- push/reply helper returns structured result

### KWN-06 - Registration Quick Reply and Flex UX

Status: planned.

Purpose:

Improve patient registration UX after KWN-02 contract and KWN-05 delivery layer are stable.

Dependencies:

- KWN-02
- KWN-05

Scope:

- registration quick replies
- profile summary Flex message
- edit/confirm actions
- resume incomplete registration
- text fallback for every step
- mobile-friendly display

Out of scope:

- survey scheduling
- HIS verification
- OTP phone verification
- LIFF app

Design principles:

- Flex summarizes and confirms; it does not replace all data entry.
- Quick Reply accelerates common choices; free text must still work.
- Do not call self-entered data verified.
- Keep the interaction simple for post-surgical patients.

Acceptance criteria:

- user can complete registration by tapping where possible
- user can still type every answer
- incomplete registration can resume
- summary Flex shows only safe fields
- full phone is not exposed unnecessarily
- accessibility text fallback exists

Regression requirements:

- first name free text
- last name free text
- HN free text
- phone normalization
- consent quick reply
- declined consent
- Flex summary after complete registration
- fallback when feature flag off

### KWN-07 - Engagement Tracking and Survey Scheduling

Status: planned.

Purpose:

Track real bot usage and send satisfaction survey links at 7, 14, 21, and 30 day milestones.

Dependencies:

- KWN-02
- KWN-04
- KWN-05
- KWN-06

Survey form:

```text
https://docs.google.com/forms/d/e/1FAIpQLSc8NM7wvIrhzo8zW6NbfvKI741KcEANGzc8BcdZsfCErqkQAQ/viewform
```

Scope:

- survey schedule rows
- opaque tracking tokens
- redirect endpoint that records click before sending user to Google Form
- delivery state
- clicked state
- failed state
- milestone 7, 14, 21, 30 days after qualifying usage
- dashboard visibility for due/sent/clicked/failed survey records

Out of scope:

- ingesting completed Google Form responses
- AI sentiment analysis
- replacing Google Forms

Main risks:

- PII in URLs
- direct Google Form links that cannot be tracked
- duplicate survey sends
- missing sends after restart
- conflating survey backlog with clinical alert backlog

Design requirement:

Survey tracking links must use opaque tokens and contain no patient PII.

Acceptance criteria:

- qualifying use creates due survey schedule
- due survey send is persistent
- survey link uses opaque token
- click is recorded
- send failure is visible
- no duplicate sends for same patient/milestone

Regression requirements:

- first qualifying use schedules milestones
- repeated use does not duplicate same milestone
- due milestone sends once
- clicked token updates clicked state
- invalid token handled safely
- failed send persists failed state

### KWN-08 - Survey Completion and Dashboard Analytics

Status: planned.

Purpose:

Connect survey response completion to the engagement lifecycle and make survey results visible without mixing them with clinical urgency.

Dependencies:

- KWN-07

Scope:

- completion matching strategy
- response and completion rate metrics
- patient survey timeline
- overdue survey filters
- dashboard analytics for sent, clicked, completed, failed

Out of scope:

- AI sentiment analysis
- full replacement of Google Forms
- clinical alert workflow

Main risks:

- mismatching form responses to patients
- exposing raw feedback too broadly
- treating survey overdue as clinical urgency

Acceptance criteria:

- sent, clicked, and completed are distinct states
- dashboard separates engagement from clinical alerts
- analytics avoid unnecessary PHI
- completion matching is deterministic

Regression requirements:

- completed response marks correct survey
- unmatched response handled safely
- duplicate completion ignored or idempotent
- analytics count each state correctly

### KWN-09 - Unified Clinical Alert and Incremental Webhook Split

Status: planned.

Purpose:

Consolidate clinical alert concepts and gradually split the large webhook into safer modules after visibility, recovery, and survey analytics are stable.

Dependencies:

- KWN-03
- KWN-08

Scope:

- clinical alert lifecycle
- assignment, acknowledgement, resolution
- shared action contracts
- incremental webhook modularization
- behavior-preserving extraction

Out of scope:

- full webhook rewrite
- microservices
- changing Dialogflow intent contracts all at once
- mixing engagement survey alerts with clinical alerts

Main risks:

- large refactor blast radius
- accidental behavior drift
- mixing clinical and engagement concepts
- breaking established Dialogflow flows

Acceptance criteria:

- extracted modules preserve current behavior
- existing tests pass
- new module boundaries are covered by tests
- rollout can be reversed by commit

Regression requirements:

- each existing intent still routes correctly
- urgent flows unchanged
- alert state transitions covered
- dashboard actions covered

## 7. Later Roadmap Expansion

The following items came from later planning discussion and should be introduced only after the current operational roadmap is stable or after `docs/MASTER_ROADMAP.md` is formally revised.

### KWN-10 - Clinical Workflow Contract

Purpose:

Make clinical assessment, risk, recommendation, escalation, and follow-up deterministic.

Scope:

- pure risk engine contract
- assessment state separate from conversation state
- escalation rules
- recommendation rules
- golden tests for risk scenarios

Acceptance criteria:

- same clinical input produces same risk and recommendation
- risk engine has no persistence side effects
- escalation rules are explicit
- failure handling is deterministic

### KWN-11 - Personalized Education Contract

Purpose:

Move education from static topic responses toward patient-profile-aware recommendation.

Scope:

- education catalog
- content versioning
- eligibility rules
- ranking rules
- regression tests for profile-to-education output

Acceptance criteria:

- profile differences produce expected education differences
- missing profile falls back safely
- content versions are auditable
- LLM polish remains optional and bounded

### KWN-12 - Conversation State Machine

Purpose:

Prevent conversation state drift across registration, assessment, education, consultation, and cancellation flows.

Scope:

- explicit state model
- timeout
- resume
- cancel
- interrupt handling
- text fallback

Acceptance criteria:

- no state dead ends
- user can resume incomplete flows
- urgent interrupt can bypass nonurgent flows
- stale state expires safely

### KWN-13 - Follow-Up Engine

Purpose:

Unify follow-up scheduling, eligibility, message generation, and response tracking.

Scope:

- event-driven follow-up creation
- persistent due records
- response and no-response tracking
- chronic or long-term follow-up policies

Acceptance criteria:

- follow-up survives restart
- duplicate reminders are prevented
- no-response escalation is auditable
- dashboard can explain why a reminder is due

### KWN-14 - Dashboard Workflow

Purpose:

Move the dashboard from read-heavy visibility to operational workflow.

Scope:

- assign
- review
- resolve
- close
- audit log
- nurse workload visibility

Acceptance criteria:

- each operator action has an audit trail
- dashboard states are deterministic
- patients and clinical alerts are not confused with surveys

### KWN-15 - Analytics Layer

Purpose:

Provide read-only operational analytics from existing contracts.

Scope:

- registration rate
- active users
- follow-up success
- assessment distribution
- high-risk trend
- survey send/click/complete funnel

Acceptance criteria:

- analytics are read-only
- no unnecessary PHI is displayed
- counts are reproducible from source rows

### KWN-16 - Performance and Reliability

Purpose:

Reduce Google Sheets load and improve reliability under production use.

Scope:

- caching review
- batch reads
- batch writes
- backoff
- bounded retry
- metrics
- contention review

Acceptance criteria:

- high-traffic flows avoid unnecessary Sheets I/O
- retry behavior is bounded
- metrics distinguish success, failure, skip, and degraded states

### KWN-17 - Security Hardening

Purpose:

Run a focused privacy and security hardening pass after major workflows stabilize.

Scope:

- log review
- PII scan
- consent audit
- CSRF and auth review
- secret handling
- rate limit review
- threat model

Acceptance criteria:

- no known PII logging path remains
- dashboard actions are authenticated and CSRF-protected
- consent boundaries are enforced
- secrets are not committed

### KWN-18 - Production Readiness

Purpose:

Make releases repeatable and supportable.

Scope:

- deployment checklist
- rollback plan
- health checks
- monitoring
- backup
- disaster recovery
- release notes
- runbook updates

Acceptance criteria:

- release can be deployed and rolled back
- health checks reflect real dependencies
- operational runbooks are current

## 8. Recommended Work Order From Today

### Step 1 - Finish KWN-02 Rework

Do not start later KWN work until KWN-02 is green.

Immediate work:

1. Fix the current `tests/test_patient_registration.py` failures.
2. Complete dashboard boundary tests.
3. Run targeted KWN-02 tests.
4. Run focused compatibility tests.
5. Run full unittest suite.
6. Run compileall.
7. Run `git diff --check`.
8. Rewrite KWN-02 `RESULT.md` as `# CODEX_RESULT`.
9. Review diff for forbidden paths.
10. Only then ask for commit approval.

### Step 2 - Commit KWN-02 Only

Commit should include only approved KWN-02 files and the active KWN-02 result file.

Do not include:

- `skills-lock.json`
- prior `.chatgpt/codex-runs/**`
- unrelated docs
- new roadmap files unless the user explicitly approves including docs in the same commit

### Step 3 - KWN-03 Manual Failed-Alert Recovery

Implement manual operator recovery before automatic retry.

Reason:

- the team needs to observe real failed-alert behavior
- manual lifecycle clarifies stale alert policy
- automatic retry before operator visibility risks duplicate sends

### Step 4 - KWN-04 Persistent Due Dispatcher

Build persistent dispatch foundation before surveys.

Reason:

- survey scheduling must survive deploys
- reminders and surveys share due-record mechanics
- persistent dispatcher reduces reliance on in-memory jobs

### Step 5 - KWN-05 and KWN-06 LINE UX Foundation

Build delivery abstraction first, then registration Quick Reply and Flex UX.

Reason:

- rich LINE payloads need a safe shared builder
- Flex summary depends on KWN-02 registration fields
- every rich interaction must retain text fallback

### Step 6 - KWN-07 and KWN-08 Engagement Surveys

Add survey scheduling, token tracking, click tracking, completion matching, and dashboard analytics.

Reason:

- patient feedback requires reliable scheduling
- direct Google Form links are not enough because they cannot track delivery/click lifecycle
- analytics should separate engagement from clinical urgency

### Step 7 - KWN-09 Consolidation

Only after manual failed-alert recovery and survey analytics are stable, consolidate clinical alert lifecycle and split webhook modules incrementally.

Reason:

- earlier split risks refactor churn
- current behavior must be pinned by tests first
- alert and survey concepts must remain separate

## 9. AI Coding Agent Prompt Template

Use this when starting any future KWN work:

```text
Repository:
C:\Kwan_LineBot\Linebot-Code\kwannurse-linebot

Branch:
codex/dashboard-command-center

Before editing:
1. Run git branch, HEAD, and status checks.
2. Confirm dirty files match the allowed pre-existing artifacts.
3. Read docs/MASTER_ROADMAP.md and docs/KWN_IMPLEMENTATION_ROADMAP.md.
4. Read the relevant source and tests.

Do not:
- stage
- commit
- push
- restore
- clean
- stash
- modify skills-lock.json
- modify unrelated .chatgpt result files
- expand into later KWN work

Work unit:
KWN-XX - <name>

Required method:
1. Add focused failing tests for the contract.
2. Implement the smallest patch.
3. Run targeted tests.
4. Run full verification required by the work unit.
5. Update the active RESULT.md.
6. Report changed files, tests, git status, and exclusions.

Completion requires:
- targeted tests pass
- full unittest passes unless explicitly scoped otherwise
- compileall passes
- git diff --check passes
- no forbidden path changed
- no staging, commit, or push
```

## 10. Commit Policy

Use one bounded reviewed work unit per local commit.

Preferred commit examples:

```text
feat: add patient registry contract
feat: add manual failed-alert recovery
feat: add persistent due dispatcher
feat: add line delivery builders
feat: add registration quick reply and flex summary
feat: add survey scheduling and tracking
feat: add survey analytics dashboard
refactor: split webhook clinical alert workflow
```

Never commit:

- `skills-lock.json` unless explicitly approved
- old `.chatgpt/codex-runs/**` files
- credentials
- generated cache files
- unrelated docs
- unrelated formatting churn

## 11. Definition of Done

A KWN work unit is done only when:

- its scope and out-of-scope items are respected
- acceptance criteria are met
- privacy boundaries are checked
- targeted tests are added or updated
- full verification passes
- result file records commands and status
- diff is reviewed for forbidden paths
- user approves staging and commit

If any blocker remains, the result file must say `blocked`, not `completed`.

## 12. Open Documentation Corrections

Known documentation drift:

- `ARCHITECTURE.md` mentions `services/llm_service.py`, while the actual file is `services/llm.py`.

This should be fixed in a small documentation-only change after the active KWN-02 rework is complete, unless the user explicitly approves doing it sooner.
