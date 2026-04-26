# 🚀 Sprint 2 Plan — Phase 2 Functional Gaps

**Goal**: ปิด **3 P0/P1 gaps** จาก `PRODUCT_VISION.md` Phase 2:

1. 📋 **Pre-consult Summary** (P0) — รวบรวม context ผู้ป่วยก่อนพยาบาลรับเคส
2. 📸 **Image Analysis** (P0) — วิเคราะห์ภาพแผลด้วย Gemini Vision
3. 🎓 **Personalized Education** (P1) — แนะนำความรู้ตาม profile (age/sex/surgery)

**Effort**: XL (~6-7 sessions) · **Branch**: `phase-3/sprint-2`

---

## 📐 Order of Execution

ทำตามลำดับนี้ — แต่ละ S2-x ต่อ branch ใหม่ → PR แยก → merge แล้วค่อยขึ้น S2-(x+1):

```
S2-1 (Pre-consult Summary)  →  S2-2 (Image Analysis)  →  S2-3 (Personalized Education)
       1-2 sessions                  2-3 sessions              2 sessions
       Reuse-heavy                   New external API          Builds on profile
```

**เหตุผลของลำดับ**:
- S2-1 เร็วที่สุด ไม่มี external dep ใหม่ ได้ value ให้พยาบาลทันที
- S2-2 ตั้ง pattern ของ Gemini Vision integration ก่อน
- S2-3 ต้องการ patient profile schema ที่อาจจะ refine ระหว่าง S2-1/S2-2

---

## 🎯 S2-1: Pre-consult Summary

**Goal**: ก่อนพยาบาล "รับเคส" → เห็น summary packet ของผู้ป่วยทันที (อาการล่าสุด, risk, reminders, queue reason).

### Files to add/modify

```
+ services/presession.py         # ขยาย: build_preconsult_packet(user_id) → dict
+ routes/dashboard/views.py      # เพิ่ม route GET /dashboard/queue/<id>/preview
+ routes/dashboard/templates/queue_preview.html   # HTMX modal/sidebar
~ routes/dashboard/templates/queue.html           # เพิ่มปุ่ม "ดูสรุป" + hx-get
+ test_preconsult_summary.py     # 8-10 tests
```

### Data shape ของ packet

```python
{
  "user_id": "U...",
  "queued_reason": "...",           # จาก teleconsult queue row
  "latest_symptoms": [...],          # 3 รายการล่าสุด (จาก SymptomLog)
  "risk_profile": {...},             # ล่าสุดจาก RiskProfile
  "active_reminders": [...],         # ที่ยัง pending
  "recent_alerts": [...],            # alert 7 วันล่าสุด
  "summary_text": "...",             # rule-based 2-3 ประโยค
}
```

### Acceptance criteria

- [ ] ใน `/dashboard/queue` มีปุ่ม "ดูสรุป" → เปิด modal ผ่าน HTMX
- [ ] Modal แสดง 5 หัวข้อข้างบน
- [ ] Cache 30s ต่อ user_id (reuse `services/cache.py`)
- [ ] หลัง assign แล้ว → log ว่าพยาบาลคนไหนเปิดดู (audit)
- [ ] 8+ tests pass

### Risks
- ❓ Performance: ดึง 5 sources จาก Sheets ต่อครั้ง → ต้องใช้ batch read หรือ cache aggressive
- ❓ PII in modal — ต้องไม่ log raw

---

## 📸 S2-2: Image Analysis (Wound Photo)

**Goal**: ผู้ป่วยส่งรูปแผลผ่าน LINE → bot ดึงรูปจาก LINE Content API → ส่งให้ Gemini Vision → ได้ severity + observations → log + alert ถ้าเสี่ยง.

### New external dependencies

```
+ google-generativeai         # already in requirements.txt (LLM service)
  → use gemini-2.0-flash-exp with image input
+ requests (existing)         # for LINE Content API GET /v2/bot/message/{id}/content
```

### Files to add/modify

```
+ services/wound_analysis.py     # core: analyze_wound_image(image_bytes) → dict
+ services/llm.py (extend)       # เพิ่ม method analyze_image() reuse circuit breaker
~ routes/webhook.py              # handle event type=image (LINE webhook)
+ database/wound_logs.py         # persist results to new sheet "WoundAnalysisLog"
~ services/notification.py       # build_wound_alert_message()
+ test_wound_analysis.py         # 10-12 tests (mock Gemini + LINE Content API)
+ dialogflow/intents/...         # อาจไม่ต้อง — image events bypass Dialogflow
```

### Flow

```
LINE event (type=image, messageId=X)
  ↓
GET https://api-data.line.me/v2/bot/message/X/content   (auth: CHANNEL_ACCESS_TOKEN)
  ↓
image_bytes (jpeg/png)
  ↓
services/wound_analysis.analyze_wound_image(bytes)
  → Gemini call with structured prompt:
    "ประเมินแผลผ่าตัด: severity (low/medium/high), observations, advice"
  → returns {severity, observations[], advice, confidence}
  ↓
database/wound_logs.save(user_id, result, image_size, timestamp)
  ↓
if severity == 'high': send_line_push(alert, NURSE_GROUP_ID)
  ↓
LINE reply to user: "เราได้รับรูปแล้ว..."
```

### Sheet schema ใหม่

```
WoundAnalysisLog:
  Timestamp | User_ID | Severity | Observations | Advice | Confidence | Image_Size_KB
```

### Acceptance criteria

- [ ] รับ image event จาก LINE webhook → ดึง bytes สำเร็จ
- [ ] Gemini Vision call สำเร็จ (mocked ใน test)
- [ ] บันทึกผลลง Sheets
- [ ] High severity → push alert พยาบาล
- [ ] Reply กลับผู้ป่วยภายใน 8 วิ (timeout budget เหมือน text LLM)
- [ ] Circuit breaker เปิด → fallback message "ระบบกำลังบำรุงรักษา ส่งรูปอีกครั้งภายหลัง"
- [ ] Daily call cap reuse `LLM_DAILY_CALL_LIMIT`
- [ ] 10+ tests pass

### Risks
- 🔴 **Cost**: Gemini Vision แพงกว่า text — ต้องตั้ง daily cap แยก (`LLM_VISION_DAILY_CAP`)
- 🔴 **Latency**: image processing 3-6s — อาจเกิน webhook 30s → consider async if push notif
- 🟡 **Image privacy**: ห้าม log image bytes / hash / Drive path เก็บถาวร
- 🟡 **LINE rate limit**: Content API มี quota แยก

---

## 🎓 S2-3: Personalized Education

**Goal**: ระบบแนะนำคู่มือสุขภาพปรับตาม **age + sex + surgery_type** ของผู้ป่วย แทนที่จะ static guide ตามเฉพาะ topic.

### Files to add/modify

```
~ services/education.py          # extend: recommend_for_profile(profile, topic) → guides
+ services/patient_profile.py    # NEW: get_or_build_profile(user_id) → dict
~ database/sheets.py             # อาจเพิ่ม helper read RiskProfile + SymptomLog
~ services/knowledge.py          # ทำ guides ให้รองรับ template variable {age_band}, {surgery}
~ routes/webhook.py              # GetKnowledge intent → resolve profile แล้วเรียก
+ test_personalized_education.py # 8-10 tests
```

### Profile resolution

```python
patient_profile = {
  "age": 58, "age_band": "55-64",
  "sex": "F",
  "surgery_type": "knee_replacement",  # จาก RiskProfile
  "diseases": ["diabetes", "hypertension"],
  "days_since_discharge": 12,  # จาก scheduled reminders
}
```

### Recommendation logic (rule + optional LLM)

```python
def recommend_for_profile(profile, topic):
  base = get_static_guide(topic)
  # rule-based augmentation
  if 'diabetes' in profile.diseases:
    base += guide_diabetes_wound_care()
  if profile.age_band in ("65+",):
    base += guide_elderly_mobility()
  # LLM optional polish (if LLM_PROVIDER!=none)
  if llm_enabled():
    base = llm.personalize_text(base, profile)
  return base
```

### Acceptance criteria

- [ ] `recommend_for_profile()` คืนเนื้อหาต่างกันสำหรับ profile ต่างกัน
- [ ] Static fallback ทำงานเมื่อไม่มี profile (ผู้ป่วยใหม่)
- [ ] LLM polish เป็น optional — `LLM_PROVIDER=none` → rule-based only
- [ ] Profile cache 5 นาที ต่อ user_id
- [ ] 8+ tests pass

### Risks
- 🟡 Profile data ไม่ครบ → fallback ต้องดี
- 🟡 LLM polish อาจ hallucinate medical advice — ต้อง prompt engineering ระวัง

---

## 🔧 Cross-Sprint Concerns

### Env vars ใหม่ที่อาจต้องเพิ่ม

```
LLM_VISION_DAILY_CAP=200        # S2-2: cap แยกสำหรับ image calls
LLM_VISION_TIMEOUT=12           # S2-2: image processing ใช้เวลานานกว่า text
PRECONSULT_CACHE_TTL=30         # S2-1: ปรับได้ภายหลัง
```

### Dialogflow changes
- S2-2 อาจไม่ต้อง intent ใหม่ (image events bypass Dialogflow)
- S2-3 ใช้ intent เดิม `GetKnowledge` + `RecommendKnowledge`

### Backward compat
- ✅ ทุก S2-x ต้อง backward compatible กับ webhook contract เดิม
- ✅ Sheets schema ใหม่ (WoundAnalysisLog) เป็น add-only — ไม่กระทบของเดิม
- ✅ ถ้าไม่ตั้ง `LLM_PROVIDER=gemini` → S2-2 จะ disable + S2-3 fallback rule-based

### Test strategy

แต่ละ S2-x:
1. เพิ่ม test file ใหม่
2. เพิ่ม entry ใน `run_regression_tests.py`
3. ทุก suite ต้องผ่านก่อน open PR

---

## 📊 Tracking

| Sprint | Status | Branch | PR | Tests Added |
|---|---|---|---|---|
| S2-1 Pre-consult Summary | ✅ Done & merged | `phase-3/sprint-2-s1` | #3 | 16 |
| S2-2 Image Analysis | ✅ Done | `phase-3/sprint-2-s2` | TBD | 28 |
| S2-3 Personalized Education | 🟡 Not started | `phase-3/sprint-2-s3` | - | 0 |

อัปเดตตารางนี้หลังแต่ละ sub-sprint เสร็จ.

---

## ✅ Definition of Done (Sprint 2 รวม)

- [ ] 3 sub-sprints PR merge เข้า `main` หมด
- [ ] Test count เพิ่ม ≥ 26 tests (8+10+8)
- [ ] All 16+ regression suites ผ่าน
- [ ] `PRODUCT_VISION.md` update — Phase 2 → ✅ done
- [ ] `README.md` feature matrix update
- [ ] Wound analysis flow ทดสอบจริงกับ LINE bot อย่างน้อย 1 ครั้ง
- [ ] Pre-consult summary ทดสอบจริงโดยพยาบาลขวัญ/จอย
- [ ] Sprint 2 plan ไฟล์นี้ถูกลบหรือ archive หลังจบ

---

_Created: 2026-04-26 · Last updated: 2026-04-26_
