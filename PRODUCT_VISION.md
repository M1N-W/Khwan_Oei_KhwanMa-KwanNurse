# KwanNurse-Bot — Product Vision & Roadmap

สรุปวิสัยทัศน์ของระบบจากเอกสารต้นทาง 2 ฉบับ พร้อม **gap analysis** ระหว่างสิ่งที่
เอกสารตั้งใจกับ implementation ปัจจุบัน. เอกสารนี้สกัดมาจาก
`END_TO_END_AUDIT.md` (Phase 2) เก็บเฉพาะส่วนที่ยังเป็น "ทิศทางผลิตภัณฑ์" —
ส่วน technical audit เก่าที่ทำเสร็จแล้วถูกตัดออก.

> 📅 อัปเดตล่าสุด: หลัง Phase 3 Sprint 1 (nurse dashboard) เสร็จสมบูรณ์

---

## 1. เอกสารต้นทาง

| เอกสาร | บทบาท |
|---|---|
| `แนวทางการสร้าง Chat bot(1).docx` | กำหนด core requirement: triage + alert + Sheets logging |
| `การออกแบบรูปแบบการพยาบาลทางไกล(2).docx` | ขยายเป็น tele-nursing platform: dashboard, teleconsult, image, education, HIS |

---

## 2. Vision สรุป (จาก doc 2 — `tele-nursing platform`)

ระบบ tele-nursing ที่ครบ **6 capabilities**:

1. ประเมินอาการผ่าน AI chatbot ทั้งแบบสอบถาม / free text / **ภาพถ่ายแผล**
2. ให้ความรู้เฉพาะรายตามเพศ อายุ และประเภทการผ่าตัด
3. ติดตามหลังจำหน่ายตามรอบ พร้อม **early warning detection**
4. ทำ **risk stratification** รายบุคคลจากหลายปัจจัย
5. มี **dashboard** สนับสนุนการตัดสินใจของพยาบาล
6. มี **teleconsult** แบบข้อความหรือวิดีโอ โดยพยาบาลเห็น AI analysis ก่อน

ระบบไม่ได้ตั้งใจให้เป็นแค่ "ตอบข้อความ" แต่เป็น **care platform** สำหรับ triage,
follow-up, decision support, และ teleconsult.

---

## 3. Requirements

### Core Functional (ต้องมี)

1. ผู้ป่วยประเมินอาการผ่าน LINE OA ด้วย structured questionnaire
2. คัดกรองความรุนแรงระดับ เบา/ปานกลาง/เร่งด่วน
3. แจ้งเตือนพยาบาลทันทีเมื่อเข้าเกณฑ์เสี่ยง
4. บันทึกข้อมูลย้อนหลังได้
5. Knowledge delivery: ดูแลแผล / กายภาพ / ป้องกันแทรกซ้อน / ยา
6. ติดตามหลังจำหน่ายตามรอบ
7. Teleconsult path สำหรับเคสที่ต้องคุยพยาบาล
8. Risk stratification รายบุคคล

### Secondary / Expansion

1. วิเคราะห์ข้อความอิสระของผู้ป่วย (free-text NLP/LLM)
2. **วิเคราะห์ภาพแผล** (image analysis)
3. Personalization ของความรู้และการติดตามตาม profile
4. **Dashboard** ให้พยาบาลเห็นสถิติ, queue, trend, risk flags
5. เชื่อมต่อช่องทางแจ้งเตือน/ระบบโรงพยาบาลเพิ่มเติม (HIS)
6. รองรับ synchronous consult ที่ richer กว่าข้อความ (video)

### Non-Functional

1. Nurse alert ต้องเร็วและเชื่อถือได้
2. Follow-up logic ต้องไม่ตกหล่นหลัง restart/deploy
3. Data logging ต้องเรียกดูย้อนหลังได้ง่าย
4. ต้อง **ลด** ภาระพยาบาลจริง ไม่ใช่เพิ่ม manual work
5. UX เรียบง่ายพอให้ผู้ป่วยหลังผ่าตัดใช้งานเอง

---

## 4. Status Map: Vision vs Implementation

### ✅ Done (มีอยู่ครบ)

- LINE OA + webhook chatbot — `routes/webhook.py`
- Symptom assessment (pain/wound/fever/mobility) — `services/risk_assessment.py`
- Risk stratification จาก demographic + disease — `calculate_personal_risk()`
- Follow-up reminders ตาม milestone — `services/reminder.py` + `services/scheduler.py`
- Teleconsult queue + nurse notification — `services/teleconsult.py`
- Static knowledge guides — `services/knowledge.py`
- **Nurse dashboard** (Phase 3 Sprint 1) — `routes/dashboard/` + `services/dashboard_*.py`
- Early warning detection (concern keyword scan) — `services/early_warning.py`
- LLM optional pathway with circuit breaker — `services/llm.py`

### 🟡 Partial (มีบางส่วน)

| Capability | Gap |
|---|---|
| Free-text analysis | มี `services/nlp.py` แต่ยังเป็น keyword/rule-based เป็นหลัก |
| Personalized education | `services/education.py` แนะนำตาม topic แต่ไม่ใช้ profile data |
| Pre-consult summary | มี `analyze_free_text` แต่ยังไม่รวมเป็น "summary packet" ให้พยาบาลก่อน engage |
| Patient longitudinal view | Dashboard มี timeline แต่ยังไม่มี trend analysis ข้าม session |

### ❌ Not Started (Roadmap ข้างหน้า)

#### Phase 2 (Close Functional Gaps)

- **Neuro-symptom branch** ในแบบสอบถามอาการ (เอกสารแม่ระบุไว้)
- **Personalized education** ตาม age / sex / surgery type
- **Pre-consult summary block** สำหรับพยาบาลก่อนเริ่ม teleconsult

#### Phase 3 (Operations Layer — เริ่มแล้วบางส่วน)

- ✅ Nurse dashboard (Sprint 1)
- ✅ Longitudinal patient view (Sprint 1 S1-3)
- ❌ **Image-based wound analysis** (Gemini Vision)
- ❌ **HIS integration adapter**
- ❌ **Historical-data risk model** (ยังเป็น one-shot rule)
- ❌ **Video consult flow**

#### Long-Term Architecture

- ❌ Persistent scheduler / external job queue (ยังเป็น `MemoryJobStore`)
- ❌ Async notification worker / outbox pattern
- ❌ Move state ออกจาก Google Sheets ไป relational/KV store
- ❌ Monitoring/alerting layer (มีแค่ in-process metrics)
- ❌ Multi-worker support (server-side session store, distributed locks)

---

## 5. แนะนำ Direction ต่อไป — `Care-Loop`

จาก 3 product directions ที่เคยพิจารณา:

- **Triage-First** — เน้น symptom triage + alerting
- **Care-Loop** — เน้น patient journey หลังจำหน่าย ⭐ **แนะนำ**
- **Nurse-Command** — เน้น dashboard + queue (Sprint 1 ทำแล้ว)

`Care-Loop` ตรงกับเอกสาร tele-nursing มากที่สุด และ reuse capability ที่มีแล้ว
(reminder + knowledge + risk + teleconsult + dashboard) ได้โดยไม่ต้องเริ่มใหม่.

### Sprint 2 Phase 3 — เสนอ work items

| Priority | Item | Effort |
|---|---|---|
| P0 | Image-based wound analysis (Gemini Vision) | M |
| P0 | Pre-consult summary packet ใน dashboard | S |
| P1 | Personalized education ตาม patient profile | M |
| P1 | Neuro-symptom capture ใน symptom flow | S |
| P2 | Persistent scheduler (Redis / SQLite job store) | M |
| P2 | HIS integration adapter (read-only seed) | L |

---

## 6. KPIs ที่ตั้งไว้ (ยังเป็นเป้าอยู่)

- Webhook latency: p50 < 400 ms (read-only), p95 < 1.5 s (write+push)
- Reminder duplicate rate = 0
- Teleconsult duplicate active-session rate = 0
- Error rate < 1% 5xx
- Core logic test coverage ≥ 80%

ปัจจุบันยังไม่มี production monitoring เพื่อวัด KPIs เหล่านี้ตรง ๆ — เป็น
roadmap item ของ Long-Term Architecture.

---

## 7. ไฟล์อ้างอิง

| ไฟล์ | บทบาท |
|---|---|
| `Readme.md` | Project overview, quick start |
| `DASHBOARD_SETUP.md` | คู่มือ setup nurse dashboard (Phase 3) |
| `DEPLOY_RUNBOOK.md` | คู่มือ deploy บน Render |
| `PRODUCT_VISION.md` | เอกสารนี้ — vision + roadmap |

---

_เอกสารนี้แทน `END_TO_END_AUDIT.md` (Phase 2) ที่เนื้อหา technical-audit ส่วนใหญ่ทำเสร็จไปแล้ว. หากต้องการ audit รอบใหม่หลัง Sprint 2 ค่อยทำเอกสารแยก เช่น `AUDIT_2026Q4.md`._
