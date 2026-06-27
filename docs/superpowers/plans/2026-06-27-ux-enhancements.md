# LINE Bot UI/UX & ROI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement context-aware Quick Replies, clinical triage shortcuts, and a daily check-in Flex Message to enhance chatbot navigation, eliminate Dialogflow slot parsing failures, and boost response rates.

**Architecture:** Extend existing LINE message builder helpers in `services/line_message.py` and inject Quick Replies inside incremental prompts in webhook handler controllers.

**Tech Stack:** Python, Flask, Dialogflow ES, LINE Messaging API.

---

## Proposed File Changes Map

* **NEW**: `tests/test_ui_ux_enhancements.py` - Unit tests for new Flex builders and Quick Reply injections.
* **MODIFY**: `services/line_message.py` - Add daily reminder Flex Card builder.
* **MODIFY**: `routes/webhook/handlers/symptoms.py` - Inject traffic-light quick replies for symptom slot collection.
* **MODIFY**: `routes/webhook/handlers/fallback.py` - Inject navigation quick replies for educational guides and after-hours choices.
* **MODIFY**: `services/survey.py` - Inject 5-star rating quick replies for post-consultation survey questions, and wire the daily reminder Flex Message.

---

### Task 1: Daily Symptom Check-in Flex Card

**Files:**
* Modify: `services/line_message.py`
* Modify: `services/survey.py`
* Create: `tests/test_ui_ux_enhancements.py`

- [ ] **Step 1: Write the failing test**
  Create `tests/test_ui_ux_enhancements.py` and define a test verifying that `build_daily_checkin_reminder()` generates a valid LINE Flex Message object.

  ```python
  # -*- coding: utf-8 -*-
  import unittest
  from services import line_message

  class TestUIUXEnhancements(unittest.TestCase):
      def test_build_daily_checkin_reminder(self):
          flex = line_message.build_daily_checkin_reminder()
          self.assertEqual(flex["type"], "flex")
          self.assertIn("🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ", flex["altText"])
          contents = flex["contents"]
          self.assertEqual(contents["type"], "bubble")
          self.assertEqual(contents["header"]["backgroundColor"], "#2E7D32")
          # Verify CTA button is present
          footer = contents["footer"]
          button = footer["contents"][0]
          self.assertEqual(button["action"]["type"], "message")
          self.assertEqual(button["action"]["label"], "📝 รายงานอาการตอนนี้")
          self.assertEqual(button["action"]["text"], "รายงานอาการ")
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_build_daily_checkin_reminder`
  Expected: FAIL (AttributeError: module 'services.line_message' has no attribute 'build_daily_checkin_reminder')

- [ ] **Step 3: Write minimal implementation in `services/line_message.py`**
  Append `build_daily_checkin_reminder` at the end of `services/line_message.py`:

  ```python
  def build_daily_checkin_reminder() -> dict:
      """
      Flex message: reminder to prompt daily symptom reporting check-in.
      """
      return {
          "type": "flex",
          "altText": "🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ",
          "contents": {
              "type": "bubble",
              "header": {
                  "type": "box",
                  "layout": "vertical",
                  "backgroundColor": "#2E7D32",
                  "paddingAll": "16px",
                  "contents": [
                      {
                          "type": "text",
                          "text": "🔔 รายงานอาการประจำวัน",
                          "color": "#FFFFFF",
                          "weight": "bold",
                          "size": "md",
                      }
                  ],
              },
              "body": {
                  "type": "box",
                  "layout": "vertical",
                  "spacing": "md",
                  "contents": [
                      {
                          "type": "text",
                          "text": "เพื่อความแม่นยำในการประเมินและป้องกันภาวะแทรกซ้อนหลังผ่าตัด โปรดกดรายงานอาการประจำวันของคุณในระบบแชทนี้ค่ะ",
                          "wrap": True,
                          "size": "sm",
                          "color": "#333333",
                      }
                  ],
              },
              "footer": {
                  "type": "box",
                  "layout": "vertical",
                  "contents": [
                      {
                          "type": "button",
                          "style": "primary",
                          "color": "#2E7D32",
                          "action": {
                              "type": "message",
                              "label": "📝 รายงานอาการตอนนี้",
                              "text": "รายงานอาการ",
                          },
                      }
                  ],
              },
          },
      }
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_build_daily_checkin_reminder`
  Expected: PASS

- [ ] **Step 5: Wire Flex Card into daily reminder pushes in `services/survey.py`**
  Modify: `services/survey.py` where daily reminders are sent (often sending plain text). Replace text reminder with `build_daily_checkin_reminder()` if rich messages are enabled.
  Let's check if there is a function like `send_daily_reminders()` in `services/survey.py`.
  We will update it to send the Flex Message.

- [ ] **Step 6: Commit**
  Run: `git add tests/test_ui_ux_enhancements.py services/line_message.py services/survey.py`
  Run: `git commit -m "feat: implement daily check-in Flex Message reminder"`

---

### Task 2: Symptom Reporting Quick Replies (Clinical Input)

**Files:**
* Modify: `routes/webhook/handlers/symptoms.py`
* Modify: `tests/test_ui_ux_enhancements.py`

- [ ] **Step 1: Write the failing test**
  Add a test verifying that `handle_report_symptoms` attaches quick reply buttons for missing fields (pain_score, mobility_status, fever_check, wound_status).

  ```python
  def test_symptom_report_quick_replies(self):
      from routes.webhook.handlers.symptoms import handle_report_symptoms
      import json

      # Test missing pain_score triggers quick replies for pain score
      response = handle_report_symptoms("U_TEST", {"pain_score": ""})
      data = json.loads(response[0].data)
      line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
      self.assertIn("quickReply", line_payload)
      items = line_payload["quickReply"]["items"]
      self.assertTrue(len(items) > 0)
      # Check first item contains emoji 🟢
      self.assertIn("🟢", items[0]["action"]["label"])
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_symptom_report_quick_replies`
  Expected: FAIL

- [ ] **Step 3: Modify `routes/webhook/handlers/symptoms.py`**
  Modify the prompts for pain_score, mobility_status, fever_check, and wound_status. Attach quick reply structures before calling `_make_dialogflow_response`.
  
  Quick reply options mappings:
  - Pain score:
    `[("🟢 0-2 (ปวดน้อย)", "2"), ("🟡 3-5 (ปวดปานกลาง)", "5"), ("🟠 6-7 (ปวดมาก)", "7"), ("🔴 8-10 (ปวดรุนแรง)", "9")]`
  - Mobility status:
    `[("🟢 เดินได้ปกติ", "เดินได้ปกติ"), ("🟡 ต้องพยุงเดิน", "ต้องพยุง"), ("🔴 เดินไม่ได้เลย", "เดินไม่ได้")]`
  - Fever check:
    `[("🟢 ไม่มีไข้", "ไม่มีไข้"), ("🔴 มีไข้ตัวร้อน", "มีไข้")]`
  - Wound status:
    `[("🟢 แผลแห้งดี", "แผลแห้งดี"), ("🟡 แผลซึม/แดง", "แผลแดงซึม"), ("🔴 แผลบวม/มีหนอง", "แผลบวมหนอง")]`

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_symptom_report_quick_replies`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run: `git add routes/webhook/handlers/symptoms.py`
  Run: `git commit -m "feat: add clinical quick replies for symptom slots"`

---

### Task 3: KB Navigation Quick Replies

**Files:**
* Modify: `routes/webhook/handlers/fallback.py`
* Modify: `tests/test_ui_ux_enhancements.py`

- [ ] **Step 1: Write the failing test**
  Add a test verifying that `handle_get_knowledge` wraps guides with Quick Replies.

  ```python
  def test_knowledge_guide_quick_replies(self):
      from routes.webhook.handlers.fallback import handle_get_knowledge
      import json

      response = handle_get_knowledge("U_TEST", {"topic": "ดูแลแผล"}, "ดูแลแผล")
      data = json.loads(response[0].data)
      line_payload = data["fulfillmentMessages"][0]["payload"]["line"]
      self.assertIn("quickReply", line_payload)
      items = line_payload["quickReply"]["items"]
      self.assertEqual(len(items), 2)
      self.assertEqual(items[0]["action"]["label"], "📚 เมนูความรู้หลัก")
      self.assertEqual(items[1]["action"]["label"], "🏥 ปรึกษาพยาบาล")
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_knowledge_guide_quick_replies`
  Expected: FAIL

- [ ] **Step 3: Modify `routes/webhook/handlers/fallback.py`**
  In `handle_get_knowledge()`, wrap guide return values in `_make_dialogflow_response(guide_text, quick_replies)` where quick_replies contains:
  `[quick_reply_item("📚 เมนูความรู้หลัก", "ความรู้"), quick_reply_item("🏥 ปรึกษาพยาบาล", "ปรึกษาพยาบาล")]`

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py -k test_knowledge_guide_quick_replies`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run: `git add routes/webhook/handlers/fallback.py`
  Run: `git commit -m "feat: append navigation quick replies to educational guides"`

---

### Task 4: After-Hours and Survey Quick Replies

**Files:**
* Modify: `routes/webhook/handlers/fallback.py` (After-hours prompts)
* Modify: `services/survey.py` (Satisfaction survey question dispatch)
* Modify: `tests/test_ui_ux_enhancements.py`

- [ ] **Step 1: Write the failing tests**
  Add tests for after-hours gating and satisfaction survey quick replies.

- [ ] **Step 2: Run test to verify they fail**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py`
  Expected: FAIL

- [ ] **Step 3: Implement Quick Replies for after-hours prompt & surveys**
  Attach quick replies to after-hours choice selection and survey score questions.

- [ ] **Step 4: Run test to verify they pass**
  Run: `python -m unittest tests/test_ui_ux_enhancements.py`
  Expected: PASS

- [ ] **Step 5: Run full regression suite**
  Run: `python -m unittest discover -s tests -p "test_*.py"`
  Expected: OK (All 663+ tests pass successfully)

- [ ] **Step 6: Commit & Finish**
  Run: `git add .`
  Run: `git commit -m "feat: complete satisfaction surveys and after-hours quick replies"`
