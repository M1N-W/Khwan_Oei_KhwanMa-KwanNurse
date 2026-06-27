# Kwan Nurse Agent Handoff Prompt

Use this prompt when continuing the project in Codex, Antigravity, or another AI coding agent.

```text
You are continuing work on the Kwan Nurse LINEBot repository.

Repository:
C:\Kwan_LineBot\Linebot-Code\kwannurse-linebot

Required first actions:

1. Read the actual repository state before planning or editing:
   - git rev-parse --abbrev-ref HEAD
   - git rev-parse HEAD
   - git status --short -uall

2. Read these project authority files before making decisions:
   - docs/MASTER_ROADMAP.md
   - docs/KWN_IMPLEMENTATION_ROADMAP.md
   - PRODUCT_VISION.md
   - ARCHITECTURE.md
   - SPRINT_2_PLAN.md

3. Treat docs/MASTER_ROADMAP.md as the committed operational source of truth.
   Treat docs/KWN_IMPLEMENTATION_ROADMAP.md as the detailed continuation guide.
   If the two disagree, stop and explain the mismatch before editing.

4. Preserve all unrelated dirty files.
   Do not restore, clean, stash, stage, commit, push, or open a pull request unless I explicitly approve that exact action.

5. Exclude these from normal commits unless I explicitly approve them:
   - skills-lock.json
   - .chatgpt/**

6. Current work order:
   - Finish and verify KWN-02 before starting KWN-03 or KWN-04.
   - Do not implement Flex Message, Quick Reply, survey scheduling, retry worker, resend, scheduler, or KWN-03+ scope while working on KWN-02.

7. KWN-02 acceptance focus:
   - PatientProfile additive schema.
   - Phone, registration status, registered timestamp, consent version, consent timestamp, last-active timestamp.
   - Storage unavailable must not crash or write.
   - Registration gate defaults off and fails open on storage outage.
   - Urgent clinical workflows must bypass the registration gate.
   - Dashboard displays patient identity and phone safely.
   - Logs must not expose raw name, HN, phone, consent value, or full LINE user id.

8. Verification commands before saying done:
   - python -m unittest tests.test_patient_registration -q
   - python -m unittest tests.test_patient_identity tests.test_personalized_education -q
   - python -m unittest discover -s tests -q
   - python -m compileall -q app.py config.py routes services database utils tests
   - git diff --check
   - git status --short -uall

9. Final response must include:
   - Files changed.
   - Implementation summary.
   - Targeted test results.
   - Full unittest result.
   - Compileall result.
   - git diff --check result.
   - Complete git status --short -uall.
   - Confirmation no Flex/Quick Reply/survey/retry worker/KWN-03+ scope was added.
   - Confirmation no stage, commit, push, restore, clean, or stash was performed.
```

