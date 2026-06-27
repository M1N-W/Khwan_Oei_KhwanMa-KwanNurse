# Design Specification — LINE Bot UI/UX & ROI Enhancements

This specification details the design for introducing Quick Replies and Flex Messages into the LINE Bot to optimize clinical workflows, reduce parsing errors, and improve patient response rates (ROI).

---

## 1. Goal & Objectives

* **Optimize Navigation**: Attach context-aware Quick Replies at the end of educational content to prevent user drop-off.
* **Streamline Clinical Reporting**: Use traffic-light coded Quick Replies for symptom prompts (pain, mobility, fever, wound) to ensure exact values and eliminate natural language processing (NLP) misinterpretations.
* **Boost Check-in Compliance**: Use a premium Flex Message for daily symptom check-in reminders, boosting patient engagement and early complication detection.
* **Automate Feedback Collection**: Attach 1-5 star ratings as Quick Replies to the post-consultation survey.
* **Reduce After-Hours Failures**: Prevent spelling errors by providing wait-vs-emergency Quick Replies during after-hours contacts.

---

## 2. Technical Details & Component Mapping

### A. Quick Replies for Knowledge Base Navigation
* **File**: [routes/webhook/handlers/fallback.py](file:///C:/Users/User/.gemini/antigravity/worktrees/kwannurse-linebot/finish-kwn-02-patient-profile/routes/webhook/handlers/fallback.py)
* **Function**: `handle_get_knowledge`
* **Enhancement**: Wrap the output of `guide_func()` inside a Dialogflow response containing Quick Replies:
  - Button 1: Label: `"📚 เมนูความรู้หลัก"`, Text: `"ความรู้"`
  - Button 2: Label: `"🏥 ปรึกษาพยาบาล"`, Text: `"ปรึกษาพยาบาล"`

### B. Quick Replies for After-Hours Choice
* **File**: [routes/webhook/handlers/fallback.py](file:///C:/Users/User/.gemini/antigravity/worktrees/kwannurse-linebot/finish-kwn-02-patient-profile/routes/webhook/handlers/fallback.py)
* **Function**: `handle_after_hours_choice` (and related routing fallback prompts)
* **Enhancement**: When prompting the after-hours choices, send the message with Quick Replies:
  - Button 1: Label: `"⏳ รอเวลาทำการ"`, Text: `"รอเวลาทำการ"`
  - Button 2: Label: `"🚨 แจ้งเรื่องฉุกเฉิน"`, Text: `"แจ้งเรื่องฉุกเฉิน"`

### C. Quick Replies for Satisfaction Survey
* **File**: `services/survey.py`
* **Enhancement**: When dispatching surveys, attach a 1-5 star rating Quick Reply block:
  - Button 5: Label: `"⭐ 5 (ดีมาก)"`, Text: `"5"`
  - Button 4: Label: `"⭐ 4 (ดี)"`, Text: `"4"`
  - Button 3: Label: `"⭐ 3 (ปานกลาง)"`, Text: `"3"`
  - Button 2: Label: `"⭐ 2 (พอใช้)"`, Text: `"2"`
  - Button 1: Label: `"⭐ 1 (ควรปรับปรุง)"`, Text: `"1"`

### D. Daily Symptom Check-in Flex Card
* **File**: `services/line_message.py` and `services/survey.py`
* **Function**: Add `build_daily_checkin_reminder()` returning a Flex Card:
  - Alt Text: `"🔔 ได้เวลารายงานอาการประจำวันแล้วค่ะ"`
  - Color theme: Green `#2E7D32`
  - Body: Explain the importance of daily reporting to track healing progress.
  - Button Action: Message action sending `"รายงานอาการ"` or `"ประเมินอาการ"`.

### E. Clinical Input Quick Replies during Symptom Collection
* **File**: [routes/webhook/handlers/symptoms.py](file:///C:/Users/User/.gemini/antigravity/worktrees/kwannurse-linebot/finish-kwn-02-patient-profile/routes/webhook/handlers/symptoms.py)
* **Function**: Add quick reply logic to symptom collection loops:
  - **Pain Score**: `🟢 0-2 (ปวดน้อย)` -> `"2"`, `🟡 3-5 (ปวดปานกลาง)` -> `"5"`, `🟠 6-7 (ปวดมาก)` -> `"7"`, `🔴 8-10 (ปวดรุนแรง)` -> `"9"`
  - **Mobility Status**: `🟢 เดินได้ปกติ` -> `"เดินได้ปกติ"`, `🟡 ต้องพยุงเดิน` -> `"ต้องพยุง"`, `🔴 เดินไม่ได้เลย` -> `"เดินไม่ได้"`
  - **Fever Check**: `🟢 ไม่มีไข้` -> `"ไม่มีไข้"`, `🔴 มีไข้ตัวร้อน` -> `"มีไข้"`
  - **Wound Status**: `🟢 แผลแห้งดี` -> `"แผลแห้งดี"`, `🟡 แผลซึม/แดง` -> `"แผลแดงซึม"`, `🔴 แผลบวม/มีหนอง` -> `"แผลบวมหนอง"`

---

## 3. Verification Plan

### Automated Tests
* Create unit tests in `tests/test_ui_ux_enhancements.py` verifying that:
  - `build_daily_checkin_reminder()` generates a valid LINE Flex Message object.
  - Fallback handlers append the correct navigation quick replies when returning knowledge.
  - Symptom incremental prompts successfully attach the expected clinical selection buttons.

### Manual Verification
* Deploy/simulate Dialogflow events for `GetKnowledge`, `ReportSymptoms`, and survey reminder triggers, confirming payloads include the designated Quick Reply structures.
