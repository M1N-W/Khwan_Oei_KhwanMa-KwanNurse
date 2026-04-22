# KwanNurse-Bot End-to-End Audit

## Executive Summary

### Document Inputs
- เอกสารอ้างอิงที่ใช้เพิ่มในการตีความ intent ของระบบ:
  - [แนวทางการสร้าง Chat bot(1).docx](</C:\Users\User\Downloads\แนวทางการสร้าง Chat bot(1).docx>)
  - [การออกแบบรูปแบบการพยาบาลทางไกล(2).docx](</C:\Users\User\Downloads\การออกแบบรูปแบบการพยาบาลทางไกล(2).docx>)
- ใช้เอกสารสองฉบับนี้เป็น `product intent source` และใช้โค้ดใน repo เป็น `implementation source`

### System Overview
- `KwanNurse-Bot` เป็น Flask webhook app ที่รับ Dialogflow intents ผ่าน [`routes/webhook.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:42) แล้วส่งงานต่อไปยัง service layer สำหรับ risk assessment, appointment, knowledge, reminder, และ teleconsult
- persistence หลักใช้ Google Sheets ผ่าน `gspread` ใน [`database/sheets.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\sheets.py:29), [`database/reminders.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:22), และ [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:30)
- outbound notification ใช้ LINE Messaging API แบบ synchronous ผ่าน [`services/notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\notification.py:18)
- background jobs ใช้ APScheduler แบบ in-process และ in-memory job store ใน [`services/scheduler.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:22)

### Biggest Problems
1. Scheduler lifecycle ยังเสี่ยงซ้ำซ้อนใน production เพราะ [`app.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:71) เรียก `create_app()` ตอน import module ทำให้ comment เรื่อง "lazy factory" ไม่สอดคล้องกับพฤติกรรมจริง
2. Reminder และ teleconsult ใช้ Google Sheets แบบ full-sheet scans และ read-modify-write หลายรอบต่อ request/job เช่น [`database/reminders.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:136), [`database/reminders.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:230), [`database\teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:111), [`database\teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:189), [`database\teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:327)
3. Teleconsult queue ไม่มี idempotency และไม่มี locking ทำให้ duplicate session, duplicate queue position, และ orphan session เกิดได้ง่ายเมื่อมี concurrent requests หรือ partial failures
4. After-hours consultation flow มี logical bug จริง: session ถูก mark เป็น `after_hours_pending` ใน [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:300) แต่ `get_user_active_session()` มองหาเฉพาะ `queued`/`in_progress` ใน [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:392)
5. Request path หลายเส้นทางทำ external I/O แบบ synchronous ทั้ง Google Sheets และ LINE push อยู่ใน HTTP request เดียว เช่น symptom, appointment, teleconsult, reminder summary
6. Service layer ปะปน business logic, persistence, aggregation, และ notification side effects ในฟังก์ชันเดียวกัน เช่น [`services/risk_assessment.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:25), [`services/appointment.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\appointment.py:13), [`services/reminder.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:114), [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:108)
7. มี duplicate module `notification.py` ที่ root และ `services/notification.py` ซึ่งมี API เดียวกันแต่ logic ต่างกัน เพิ่ม drift risk และ load-order ambiguity ([`notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\notification.py:18), [`services/notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\notification.py:18))
8. เอกสารและ test drift สูง: README ยังบอก v3 ([`Readme.md`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\Readme.md:1)), test scripts hardcode path ภายนอก Linux-style ([`test_bug_fixes.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_bug_fixes.py:12), [`test_teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_teleconsult.py:7))
9. Debug route เปิดเผย `NURSE_GROUP_ID` ตรง ๆ ใน response ([`routes/webhook.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:389))
10. Current tests แยกไม่ได้ชัดว่า failure มาจาก logic หรือ environment และมี failing expectations ที่ไม่ตรงกับ implementation ปัจจุบัน

### Fix First
- อันดับ 1: แยก scheduler startup ออกจาก import path และกำหนด single-owner execution
- อันดับ 2: แก้ teleconsult after-hours status mismatch และ partial-failure/orphan-session path
- อันดับ 3: ลด Google Sheets full scans และ `update_cell` หลายครั้งต่อธุรกรรมให้เป็น batch/repository-level operations
- อันดับ 4: ทำ notification/persistence ออกจาก request critical path เท่าที่ทำได้โดยไม่ rewrite ใหญ่
- อันดับ 5: จัดการ duplicate module, stale docs, และ drift tests เพื่อให้ refactor รอบถัดไปวัดผลได้จริง

## Document Summary And Intent

### Summary: แนวทางการสร้าง Chat bot(1).docx
- เป้าหมายหลักคือให้ผู้ป่วยประเมินอาการผ่าน LINE OA ได้เอง, ใช้ AI/Rule-based วิเคราะห์ผล, แจ้งเตือนพยาบาลทันทีเมื่อพบอาการเสี่ยง, และลดภาระงานประเมินหน้างาน
- องค์ประกอบระบบที่เอกสารตั้งใจไว้:
  - LINE OA เป็น front door
  - Dialogflow หรือ LIFF เป็น conversational/form layer
  - AI วิเคราะห์ได้ทั้ง structured answers และ free-text
  - แจ้งเตือนออกไปยัง LINE group และเก็บข้อมูลลง Google Sheets
  - รองรับการขยายไปสู่ HIS และ image-based wound analysis
- Flow หลักตามเอกสารคือ: ผู้ป่วยกดประเมิน -> ตอบคำถาม 4-5 ข้อ -> AI วิเคราะห์ -> ถ้าปกติตอบกลับทันที, ถ้าเสี่ยงแจ้งพยาบาล -> บันทึกข้อมูล
- คำถามหลักที่เอกสารระบุ: pain, wound status, fever, mobility, neuro symptoms
- เครื่องมือที่เอกสารแนะนำ: LINE Messaging API, Dialogflow/LIFF, GPT API หรือ rule-based, Google Sheet/Firebase, LINE Notify/Email/HIS integration

### Summary: การออกแบบรูปแบบการพยาบาลทางไกล(2).docx
- วาง vision ของระบบ tele-nursing ครบกว่าตัว chatbot เดิม โดยรวม 6 capability:
  - ประเมินอาการผ่าน AI chatbot ทั้งแบบสอบถาม, free text, และภาพถ่ายแผล
  - ให้ความรู้เฉพาะรายตามเพศ อายุ และประเภทการผ่าตัด
  - ติดตามหลังจำหน่ายตามรอบ พร้อม early warning detection
  - ทำ risk stratification รายบุคคลจากหลายปัจจัย
  - มี dashboard สนับสนุนการตัดสินใจของพยาบาล
  - มี teleconsult แบบข้อความหรือวิดีโอ โดยพยาบาลเห็นข้อมูล AI analysis ก่อน
- เอกสารนี้ชัดเจนว่าระบบไม่ได้ต้องการแค่ตอบข้อความ แต่ต้องเป็น care platform สำหรับ triage, follow-up, decision support, และ teleconsult

## Document-To-Code Comparison

### What Already Matches
- LINE OA + webhook chatbot path มีอยู่จริงผ่าน [`routes/webhook.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:63)
- symptom assessment ตาม pain/wound/fever/mobility มีอยู่จริงใน [`handle_report_symptoms()`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:118) และ [`calculate_symptom_risk()`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:25)
- risk stratification จาก demographic + disease factors มีอยู่จริงใน [`calculate_personal_risk()`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:181)
- follow-up reminders ตาม milestone มีอยู่จริงใน [`config.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\config.py:91) และ [`services/reminder.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:114)
- teleconsult queue และ nurse notification มีอยู่จริงใน [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:108)
- knowledge guides มีอยู่จริงใน [`services/knowledge.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\knowledge.py:9)

### Partial Matches
- เอกสารต้องการ AI วิเคราะห์ free-text; โค้ดปัจจุบันยังเป็น rule-based เป็นหลัก แม้เอกสารและคอมเมนต์จะเปิดทางไว้
- เอกสารต้องการติดตาม early warning ต่อเนื่อง; โค้ดมี reminder + concern keyword scan แต่ยังไม่ใช่ predictive or trend-based detection
- เอกสารต้องการความรู้เฉพาะราย; โค้ดปัจจุบันให้ static guide ตาม topic ไม่ personalize ตาม age/sex/surgery
- เอกสารต้องการ alert integration หลายช่องทาง; โค้ดปัจจุบันมี LINE push + Google Sheets เท่านั้น

### Gaps Against Intended Product
- ไม่มี image-based wound analysis ตามทั้งสองเอกสาร
- ไม่มี NLP/free-text analysis layer ที่แยกจาก simple keyword/rule checks
- ไม่มี dashboard หรือ nurse back-office summary view ตามเอกสาร tele-nursing
- ไม่มี patient-level trend analysis หรือ historical decision support
- ไม่มี video consult flow หรือ integration; current teleconsult เป็น queue + text escalation only
- ไม่มี HIS integration
- ไม่มี patient profile personalization layer สำหรับ content and follow-up intensity
- ไม่มี explicit neuro-symptom capture ใน symptom flow แม้เอกสารแรกระบุไว้

## Requirements Extracted From Documents

### Core Functional Requirements
1. ผู้ป่วยต้องประเมินอาการผ่าน LINE OA ได้ด้วย structured questionnaire
2. ระบบต้องคัดกรองความรุนแรงอย่างน้อยระดับ เบา/ปานกลาง/เร่งด่วน หรือ equivalent
3. ระบบต้องแจ้งเตือนพยาบาลทันทีเมื่อเข้าเกณฑ์เสี่ยง
4. ระบบต้องบันทึกข้อมูลการประเมินและการติดตามย้อนหลังได้
5. ระบบต้องมี knowledge delivery สำหรับการดูแลแผล, กายภาพ, ป้องกันภาวะแทรกซ้อน, และยา
6. ระบบต้องติดตามหลังจำหน่ายตามรอบเวลา
7. ระบบต้องมี teleconsult path สำหรับเคสที่ต้องคุยกับพยาบาล
8. ระบบต้องรองรับ risk stratification รายบุคคล

### Secondary / Expansion Requirements
1. วิเคราะห์ข้อความอิสระของผู้ป่วย
2. วิเคราะห์ภาพแผล
3. ทำ personalization ของความรู้และการติดตามตาม profile ผู้ป่วย
4. มี dashboard ให้พยาบาลเห็นสถิติ, queue, trend, และ risk flags
5. เชื่อมต่อช่องทางแจ้งเตือน/ระบบโรงพยาบาลเพิ่มเติม
6. รองรับ synchronous consult ที่ richer กว่าข้อความ

### Non-Functional Requirements Implied By Documents
1. Nurse alert ต้องเร็วและเชื่อถือได้
2. Follow-up logic ต้องไม่ตกหล่นหลัง restart หรือ deploy
3. Data logging ต้องเรียกดูย้อนหลังได้ง่าย
4. ระบบต้องลดภาระพยาบาลจริง ไม่ใช่เพิ่ม manual reconciliation work
5. UX ต้องเรียบง่ายพอสำหรับผู้ป่วยหลังผ่าตัดใช้งานเอง

## Updated Gap Analysis

### Critical Gaps
- เอกสารตั้งใจให้ระบบเป็น tele-nursing platform แต่ implementation ปัจจุบันยังเป็น webhook bot + Sheets orchestration มากกว่า platform
- nurse-facing operations ยังไม่มี dashboard ทำให้ requirement ด้าน decision support ยังไม่เริ่ม
- performance/reliability ของ reminder และ teleconsult ยังไม่ถึงระดับ production-safe สำหรับงานติดตามผู้ป่วย

### High-Impact Gaps
- symptom questionnaire ยังขาด neuro-symptom branch ที่เอกสารแรกระบุ
- knowledge system ยังไม่ personalized ตาม patient profile
- teleconsult ไม่มี pre-consult summary packet ให้พยาบาลเห็นก่อนคุย แม้เอกสาร tele-nursing ตั้งใจไว้
- risk engine ยังไม่รวม treatment history or longitudinal data

### Creative / Product Direction (3 Directions)
- Direction 1: `Triage-First`
  - เน้นทำระบบประเมินอาการ + early warning + alerting ให้แข็งแรงที่สุดก่อน
  - สีหลัก `#0F766E`, `#DC2626`, `#F59E0B`
  - severity badges ใช้ fade `0.18s ease-out`
  - suitable กับ stack ปัจจุบันที่สุด
- Direction 2: `Care-Loop`
  - เน้น patient journey หลังจำหน่าย: reminder, education, escalation, teleconsult
  - สีหลัก `#1D4ED8`, `#0F766E`, `#E5E7EB`
  - reminder cards animate `0.24s ease-in-out`
  - เหมาะถ้าจะเพิ่ม personalization และ follow-up summary
- Direction 3: `Nurse-Command`
  - เน้น dashboard + queue + risk board สำหรับทีมพยาบาล
  - สีหลัก `#111827`, `#2563EB`, `#EF4444`
  - queue status pulse `1.2s ease-in-out`
  - เหมาะกับ phase ที่เริ่มมี back-office feature
- Recommended: `Care-Loop`
  - เพราะตรงกับเอกสาร tele-nursing มากที่สุดและ reuse ความสามารถที่มีอยู่แล้วใน reminder, knowledge, risk, และ teleconsult ได้โดยไม่ต้องเริ่มใหม่

## Architecture Walkthrough

### Entry Points And Initialization Order
1. WSGI import โหลด [`app.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:16)
2. Module import สร้าง `application = create_app()` ทันทีที่บรรทัด [`app.py:71`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:71)
3. `create_app()` ลงทะเบียน routes ผ่าน [`register_routes`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:36)
4. `create_app()` เรียก `init_scheduler()` ใน [`app.py:40`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:40)
5. `init_scheduler()` start APScheduler, register daily no-response job, แล้ว load reminders จาก Sheets ใน [`services/scheduler.py:38-57`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:38)

ผลคือ scheduler ถูกผูกกับ app import lifecycle ไม่ใช่ runtime ownership ที่ชัดเจน

### Dependency Graph
```text
app.py
  -> routes/__init__.py
    -> routes/webhook.py
      -> utils/__init__.py
        -> utils/parsers.py
      -> services/__init__.py
        -> services/risk_assessment.py
          -> database/__init__.py
            -> database/sheets.py
          -> services/notification.py
        -> services/appointment.py
          -> database/__init__.py
          -> services/notification.py
        -> services/knowledge.py
        -> services/reminder.py
          -> database/reminders.py
          -> services/notification.py
          -> services/scheduler.py (deferred import)
        -> services/scheduler.py
          -> database/reminders.py
          -> services/reminder.py (deferred import)
        -> services/teleconsult.py
          -> database/teleconsult.py
          -> services/notification.py
config.py
  -> shared constants, env, logger, sheet names, risk maps, queue rules

External systems
  -> Google Sheets via gspread
  -> LINE Messaging API via requests.post
```

### Main Flows

#### ReportSymptoms
1. `/webhook` parse JSON, extract `intent`, `params`, `user_id` in [`routes/webhook.py:63-82`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:63)
2. Route เข้า `handle_report_symptoms()` ใน [`routes/webhook.py:118`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:118)
3. Validate required fields ใน route layer
4. Call `calculate_symptom_risk()` ใน [`services/risk_assessment.py:25`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:25)
5. Service คำนวณ score, build human message, save to Sheets ผ่าน `save_symptom_data()`, แล้ว conditional LINE push ถ้า high risk
6. Route return `fulfillmentText`

#### AssessRisk
1. Route parse demographic params ใน [`routes/webhook.py:145`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:145)
2. Service normalize diseases, calculate BMI/risk, persist profile, conditional LINE push ใน [`services/risk_assessment.py:181`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:181)
3. Return message ตรงจาก service

#### RequestAppointment
1. Route parse date/time/phone ผ่าน parser utils ใน [`routes/webhook.py:172-225`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:172)
2. `create_appointment()` persist ลง Appointments sheet และ push notification ไปกลุ่มพยาบาลใน [`services/appointment.py:13-55`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\appointment.py:13)
3. Return confirmation หลัง external side effects เสร็จแล้ว

#### GetKnowledge
1. Route map topic string -> guide function ใน [`routes/webhook.py:228-285`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:228)
2. Static content ถูกสร้างใน `services/knowledge.py`
3. ไม่มี persistence

#### GetFollowUpSummary
1. Route call `get_reminder_summary()` ใน [`routes/webhook.py:294-387`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:294)
2. Service ดึง scheduled reminders ทั้งหมด, filter ใน app layer, ดึง pending reminders แยก, aggregate counts ใน [`services/reminder.py:334-380`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:334)
3. Route สร้าง response text อีกชั้น

#### ContactNurse / AfterHoursChoice / CancelConsultation
1. Route parse category/description แล้ว call teleconsult service ใน [`routes/webhook.py:394-448`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:394)
2. `start_teleconsult()` เช็ค active session, queue size, create session, add queue row, notify nurse, build response ใน [`services/teleconsult.py:108-216`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:108)
3. Emergency path skip queue แต่ create session ใหม่และ update status แยกใน [`services/teleconsult.py:219-277`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:219)
4. After-hours path save session แล้ว set `after_hours_pending` ใน [`services/teleconsult.py:280-333`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:280)
5. Follow-up choice พยายามหา active session ผ่าน `get_user_active_session()` แล้ว branch ต่อใน [`services/teleconsult.py:336-390`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:336)
6. Cancel path update session status และ remove queue row ใน [`services/teleconsult.py:395-427`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:395)

#### Scheduler Path
1. `init_scheduler()` start scheduler ใน [`services/scheduler.py:33-64`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:33)
2. `load_pending_reminders()` โหลด scheduled rows ทั้งหมดจาก Sheets, parse datetime, create in-memory jobs ใน [`services/scheduler.py:78-140`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:78)
3. เมื่อ trigger, `send_reminder()` ส่ง LINE push ไป user และ append sent row ใน [`services/reminder.py:80-111`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:80)
4. Daily job `check_and_alert_no_response()` scan follow-up sheet ทั้งหมดอีกครั้ง และส่ง alert ไป nurse ใน [`services/reminder.py:282-331`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:282)

## Risk Sheet

### Blast Radius
- Webhook path: route-layer validation, parameter parsing, and synchronous persistence/notification are tightly coupled. Change ใน service เดียวกระทบ response timing และ error semantics ของผู้ใช้ทันที
- Scheduler path: any bug ใน scheduler ownership, in-memory state, หรือ date parsing สามารถทำให้ duplicate reminders, missed reminders, หรือ silent skip หลัง restart
- Teleconsult path: queue/session split across two sheets ทำให้ partial failure เกิด inconsistent state ได้ง่าย และกระทบทั้ง patient-facing latency กับ nurse workload
- Shared notification/data layer: `services/notification.py` และ `database/*` ถูกเรียกแทบทุก use case. Regression ที่นี่กระทบหลาย features พร้อมกัน

### Load Order / Initialization Order
- `config.py` load ตอน import และ freeze env-derived constants ตั้งแต่เริ่ม process
- `app.py` import `services.scheduler`, จากนั้น `application = create_app()` trigger route registration และ scheduler startup ทันที ([`app.py:16-19`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:16), [`app.py:71`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:71))
- deferred imports ใน reminder/scheduler ช่วยเลี่ยง circular import แต่ซ่อน dependency จริงและลด testability
- sheet client cache อยู่ใน module global ของ [`database/sheets.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\sheets.py:23) แต่ spreadsheet/worksheet handles ไม่ได้ cache ทำให้ทุก operation ยัง reopen workbook

### Hidden Coupling
- Status strings เป็น magic strings กระจายทั้ง services และ database เช่น `scheduled`, `sent`, `responded`, `no_response`, `queued`, `in_progress`, `after_hours_pending`, `removed`
- Sheet header names ถูก hardcode หลายที่ เช่น `Status`, `Timestamp`, `Created_At`, `Queue_Position`, `Assigned_Nurse`
- `handle_after_hours_choice()` พึ่ง `get_user_active_session()` แต่ DB helper ไม่คืน `after_hours_pending` sessions ทำให้ flow แตก
- Route layer สร้าง message บางส่วน ขณะที่ service layer สร้าง message อีกหลายส่วน ทำให้ presentation logic กระจาย
- `services/__init__.py` import รวมทุก service ทำให้ route import path หนักเกินความจำเป็น และซ่อน call graph จริง

### Tech Debt
- Duplicate notification modules ที่ root และ `services/`
- README ยังค้างที่ v3 แม้ app/logging บอก v4
- test scripts ผูก path ภายนอก repo และ terminal encoding
- `webhook_followup_handler.py` เป็น patch note artifact ไม่ใช่ runtime module แต่ยังอยู่ใน repo tracked set
- ไม่มี structured metrics, retry budget, circuit breaker, rate limit policy, หรือ dead-letter handling

### Exit Criteria Definition
- Performance budget:
  - webhook p50 < 400 ms สำหรับ read-only / content-only intents
  - webhook p95 < 1.5 s สำหรับ intents ที่แตะ Sheets write + LINE push
  - reminder send duplicate rate = 0
  - teleconsult duplicate active-session rate = 0
- Reliability budget:
  - error rate < 1%
  - no orphan session rows after teleconsult failures
  - scheduler restart must not lose scheduled reminders
- Quality budget:
  - core logic test coverage >= 80%
  - drift tests removed or rewrittenให้ใช้ local repo paths เท่านั้น
- Rollback:
  - แยก refactor เป็น module-scoped patches
  - รักษา webhook contract เดิม
  - toggle scheduler/queue changes ด้วย config flag หรือ deploy-stage gating
  - preserve current sheet schema จนกว่า migration verification จะผ่าน

## Performance Audit

### Bottleneck 1: Full-sheet scans on hot paths
- Evidence:
  - reminders read full sheet in [`database/reminders.py:136`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:136), [`database/reminders.py:230`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:230), [`database/reminders.py:279`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:279), [`database/reminders.py:325`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:325), [`database/reminders.py:367`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:367)
  - teleconsult read full sheet in [`database/teleconsult.py:111`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:111), [`database/teleconsult.py:189`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:189), [`database/teleconsult.py:327`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:327), [`database/teleconsult.py:379`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:379)
- Cause: ใช้ Google Sheets เป็น datastore แล้ว filter/sort ใน Python ทุกครั้ง
- Impact: latency โตตามจำนวน rows, throughput ต่ำ, และ quota/API round trips สูง
- Fix: ทำ repository helpers ที่ fetch ช่วงข้อมูลแคบลง, cache worksheet handle, ลด full scans เหลือ query-by-key simulation หรือ move hot entities ไป transactional store
- ROI: สูง
- Risk: กลาง

### Bottleneck 2: Multi-call row updates
- Evidence:
  - `update_session_status()` ใช้ `update_cell()` ซ้ำ 1-4 ครั้งต่อ logical update ใน [`database/teleconsult.py:211-226`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:211)
  - `check_no_response_reminders()` update row แล้ว update schedule status อีก call ใน [`database/reminders.py:397-409`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:397)
- Cause: ไม่มี batch mutation abstraction
- Impact: per-request network round trip สูง, race window กว้าง
- Fix: batch update ต่อธุรกรรม, centralize row mapping, combine status/timestamp/note writes
- ROI: สูง
- Risk: ต่ำ

### Bottleneck 3: Spreadsheet reopen on every operation
- Evidence:
  - ทุก save/get helper เรียก `client.open(SPREADSHEET_NAME)` ใหม่ เช่น [`database/sheets.py:81`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\sheets.py:81), [`database/reminders.py:42`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:42), [`database/teleconsult.py:49`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:49)
- Cause: cache เฉพาะ client, ไม่ cache spreadsheet/worksheet handles
- Impact: redundant API overhead และ repeated object creation
- Fix: add cached `get_spreadsheet()` / `get_worksheet(sheet_name)` with TTL invalidation
- ROI: สูง
- Risk: ต่ำ

### Bottleneck 4: Synchronous external I/O inside request critical path
- Evidence:
  - `requests.post` อยู่ใน [`services/notification.py:48`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\notification.py:48)
  - services เรียก notification และ sheet writes ก่อน return response
- Cause: webhook response path ทำงานเหมือน orchestrator + worker พร้อมกัน
- Impact: high tail latency, retry storms, and user-visible slowness when LINE or Sheets ช้า
- Fix: split into fast acknowledgment + async/outbox for notification and non-essential writes
- ROI: สูง
- Risk: กลาง

### Bottleneck 5: In-memory scheduler
- Evidence:
  - `MemoryJobStore()` ใน [`services/scheduler.py:23-30`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:23)
- Cause: persistent source of truth อยู่ใน Sheets แต่ live jobs อยู่ใน memory only
- Impact: restart ต้อง reload ทั้งหมด, worker multiplicity คุมยาก, missed or duplicate sends มีโอกาสสูง
- Fix: single scheduler owner + persistent job store หรือ external scheduler/queue
- ROI: สูง
- Risk: สูง

### Race Conditions And Consistency Gaps
- `start_teleconsult()` เช็ค active session ก่อน create แต่ไม่มี lock ระหว่าง [`services/teleconsult.py:123-169`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:123)
- `add_to_queue()` คำนวณ `queue_position` จาก current row count แล้ว append ทีหลัง ใน [`database/teleconsult.py:110-149`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:110)
- `create_session()` สำเร็จแต่ `add_to_queue()` fail จะทิ้ง session orphan ในสถานะ `queued`
- after-hours choice จะไม่เจอ session ที่เพิ่ง save เพราะ helper filter ไม่รวม `after_hours_pending`
- emergency escalation สร้าง session ใหม่แทน update session เดิม ทำให้ duplicate intent trail
- scheduler startup กับ web import path อาจแตกต่างกันระหว่าง local run กับ gunicorn workers

### GC Churn / Repeated Allocations / Parsing
- repeated `datetime.strptime()` ใน route/service/db หลายจุด เช่น [`services/scheduler.py:108`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:108), [`services/teleconsult.py:43-44`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:43), [`utils/parsers.py:35`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\utils\parsers.py:35)
- repeated `dict(zip(headers, row))` ใน loops ขนาดใหญ่ทุก request/job
- message strings ถูก compose ใหม่ทุกครั้งด้วย concatenation และ f-strings ยาว แต่ผลกระทบรองเมื่อเทียบกับ network I/O

### Failure Handling Gaps
- ไม่มี retry/backoff strategy ที่ชัดเจนสำหรับ Sheets หรือ LINE API
- ไม่มี circuit breaker หรือ bulkhead แยก notification path
- ไม่มี dead-letter strategy สำหรับ reminder send fail
- ไม่มี idempotency key ใน reminder send หรือ teleconsult create
- timeout มีเฉพาะ LINE push (`timeout=8`) แต่ไม่มี global request budget

## Refactor Roadmap

### Quick Wins (1-2 Days)
1. ย้าย scheduler ownership ออกจาก import-time side effect. เป้าหมายคือไม่ให้ `application = create_app()` เริ่ม scheduler ในทุก import path
2. แก้ `get_user_active_session()` ให้รองรับ `after_hours_pending` หรือสร้าง helper ใหม่สำหรับ after-hours lookup
3. ใน teleconsult path ถ้า `add_to_queue()` fail ให้ rollback session status เป็น `cancelled` หรือ `queue_failed`
4. สร้าง status constants module สำหรับ reminder/teleconsult strings
5. เพิ่ม worksheet cache เพื่อลด `client.open()` และ `worksheet()` ซ้ำ
6. เปลี่ยน multi-`update_cell()` ให้เป็น `batch_update()` ทุกจุดที่เขียนหลาย field
7. ลบหรือ deprecate root-level `notification.py` ให้เหลือ implementation เดียว
8. ปิดหรือ guard debug route `GetGroupID`
9. ปรับ tests ให้ใช้ repo-local imports และ `PYTHONIOENCODING=utf-8` หรือเลิกพึ่ง emoji output

### Medium-Term (1-2 Weeks)
1. แยก repository layer สำหรับ Google Sheets โดยให้ service ไม่รู้ header names และ row shape โดยตรง
2. สร้าง transaction-like wrappers สำหรับ teleconsult create+queue และ reminder sent+status update
3. แยก presentation building ออกจาก persistence/notification ใน services สำคัญ
4. ทำ structured logging พร้อม correlation ids เช่น `user_id`, `intent`, `session_id`, `job_id`
5. ทำ runtime config validation ตอน startup เพื่อ fail fast เมื่อ credentials/token หาย
6. ย้าย test scripts เป็น pytest/unittest จริง พร้อม mocks ของ Sheets/LINE

### Long-Term Architecture
1. ย้าย queue/session/reminder state ออกจาก Google Sheets ไป relational store หรือ key-value store ที่รองรับ atomic operations
2. ย้าย notification เป็น async worker/outbox
3. ใช้ persistent scheduler หรือ external job queue
4. แยก admin/back-office workflows ออกจาก patient-facing webhook
5. เพิ่ม monitoring/alerting สำหรับ webhook latency, send failures, scheduler lag, duplicate sessions

### Document-Driven Product Roadmap

#### Phase 1: Stabilize Existing Tele-Nursing Core
- เป้าหมาย: ทำให้สิ่งที่เอกสารต้องการและโค้ดมีอยู่แล้วใช้งานได้เสถียรจริง
- งานหลัก:
  - harden symptom triage
  - harden reminders
  - harden teleconsult queue
  - harden nurse alert path
- ตัวชี้วัด:
  - no duplicate reminders
  - no lost after-hours requests
  - predictable alert latency

#### Phase 2: Close High-Value Functional Gaps
- เป้าหมาย: ปิดช่องว่างสำคัญระหว่าง product intent กับ implementation
- งานหลัก:
  - เพิ่ม neuro-symptom capture ใน symptom flow
  - เพิ่ม patient-specific education recommendations
  - เพิ่ม pre-consult summary block สำหรับพยาบาลจาก latest symptoms, risk tier, reminders, และ queue reason
  - เพิ่ม nurse summary export / dashboard-ready aggregation layer
- ตัวชี้วัด:
  - questionnaire coverage ตรงเอกสารมากขึ้น
  - teleconsult มี patient context ก่อน nurse engage
  - knowledge path ใช้ profile data ได้จริง

#### Phase 3: Build Tele-Nursing Operations Layer
- เป้าหมาย: เปลี่ยน bot จาก request processor เป็น care operations platform
- งานหลัก:
  - nurse dashboard
  - longitudinal patient view
  - richer risk model using historical data
  - image-analysis integration seam
  - HIS/integration adapter
- ตัวชี้วัด:
  - พยาบาลติดตามและตัดสินใจจาก consolidated view ได้
  - bot ใช้ historical context แทน one-shot rule execution อย่างเดียว

## Concrete Patch Suggestions

### Patch 1: Fix scheduler ownership
- Files: [`app.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:25), [`services/scheduler.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:33)
- Before: import module แล้วเริ่ม scheduler ทันทีผ่าน `application = create_app()`
- After: expose pure app factory และแยก explicit scheduler bootstrap ที่เรียกเฉพาะ worker/process ที่เป็น scheduler owner
- Minimal diff:
  - เปลี่ยน WSGI entry ให้ใช้ factory-compatible pattern
  - gate `init_scheduler()` ด้วย env flag เช่น `RUN_SCHEDULER=true`

### Patch 2: Fix after-hours active-session lookup
- Files: [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:361), [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:336)
- Before: after-hours session ถูก save แต่ lookup helper ไม่คืน session นั้น
- After: helper รองรับ `after_hours_pending` หรือมี helper dedicated สำหรับ after-hours choice
- Minimal diff:
  - ขยาย accepted statuses เป็น `['queued', 'in_progress', 'after_hours_pending']`
  - หรือเพิ่ม `get_user_latest_session(user_id, statuses=None)`

### Patch 3: Make teleconsult create+queue consistent
- Files: [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:167), [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:102)
- Before: create session สำเร็จแล้ว add queue fail -> orphan queued session
- After: rollback status หรือ mark explicit failure state
- Minimal diff:
  - หลัง `if not queue_info` ให้ call `update_session_status(session_id, 'cancelled', notes='Queue insert failed')`
  - log correlation ด้วย `session_id`

### Patch 4: Cache spreadsheet/worksheet handles
- Files: [`database/sheets.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\sheets.py:23)
- Before: cache แค่ client
- After: add TTL cache for spreadsheet object and worksheet handles
- Minimal diff:
  - เพิ่ม `_spreadsheet` และ `_worksheet_cache`
  - helper `get_spreadsheet()` และ `get_worksheet(sheet_name)`
  - replace direct `client.open(...).worksheet(...)` calls

### Patch 5: Batch update multi-field writes
- Files: [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:197), [`database/reminders.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:238)
- Before: `update_cell()` หลายครั้งต่อ row update
- After: single `batch_update()` per logical change
- Minimal diff:
  - สร้าง helper แปลง column index -> A1 notation แบบ reuse
  - เขียน status/timestamp/nurse/notes เป็น batch เดียว

### Patch 6: Remove duplicate notification implementation
- Files: [`notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\notification.py:1), [`services/notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\notification.py:1)
- Before: มีสองโมดูล implementation ใกล้เคียงกัน
- After: เหลือโมดูลเดียว และโมดูลที่เก่า re-export หรือถูกลบ
- Minimal diff:
  - ถ้ายังต้องคง import compatibility ให้ root `notification.py` import from `services.notification`

### Patch 7: Harden tests and docs
- Files: [`test_bug_fixes.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_bug_fixes.py:12), [`test_teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_teleconsult.py:7), [`Readme.md`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\Readme.md:1)
- Before: tests hardcode external paths and stdout emoji assumptions; docs still v3
- After: tests import relative to current repo and docs reflect v4
- Minimal diff:
  - replace `sys.path.insert/append` hacks with repo-relative path resolution
  - drop emoji-only assertion output dependency
  - update README version, features, and deployment instructions

## Exit Criteria

### KPI Targets
- Webhook latency:
  - p50 < 400 ms for `GetKnowledge`, validation failures, and summary reads after caching
  - p95 < 1.5 s for `ReportSymptoms`, `AssessRisk`, `RequestAppointment`, `ContactNurse`
- Throughput:
  - sustain 20 concurrent webhook requests without duplicate teleconsult sessions
  - scheduler restart must reschedule 100% of pending reminders
- Memory:
  - stable process RSS over 24h, no unbounded growth from scheduler/job reload
- Error rate:
  - <1% 5xx/exceptional webhook completions
  - 0 duplicate reminder sends from worker duplication
- Coverage:
  - >=80% on core service/repository logic
  - mandatory tests for teleconsult create/rollback, after-hours choice, reminder send/update, scheduler bootstrap gating

### Rollback Plan
- deploy patch sets independently
- preserve webhook contract and sheet schema during rollout
- enable scheduler gating through env flag
- if new repository batching fails, revert repository helper module only while keeping status constants and test fixes

## Validation Results

### Static Validation
- Tracked files found: 27 via `git ls-files`
- Import graph resolved successfully after dependency install
- Key entry points, service modules, data modules, tests, and drift artifacts all inspected

### Runtime Validation
- `python -m pip install -r requirements.txt` succeeded
- Import smoke test passed for all main modules after install
- `python test_bug_fixes.py` initially failed because CP874 console encoding could not print emoji
- rerun with `PYTHONIOENCODING=utf-8` showed real failures:
  - Bug 2 test patches `services.reminder.get_scheduled_reminders`, but implementation imports it inside function from `database.reminders`, so test target is stale
  - Bug 3 expectation for follow-up summary message diverges from current implementation and external dependencies leaked into run
- `python test_teleconsult.py` passes only superficial logic checks; database operations fail without credentials but the script still prints "ALL TELECONSULT TESTS COMPLETED", so it is not a trustworthy regression gate

## Folder And File Walkthrough

### Root

#### [`app.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\app.py:1)
- Role: main entry point, route registration, scheduler init
- Connected to: `routes`, `services.scheduler`, `config`
- Problems:
  - scheduler side effect on module import
  - comment claims lazy factory but `application = create_app()` negates it
- Impact: duplicate scheduler risk, startup coupling
- Minimal diff: gate scheduler bootstrap by env flag or split scheduler runner
- Urgency: critical

#### [`config.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\config.py:1)
- Role: env config, constants, logger, sheet names, risk/queue metadata
- Connected to: almost every module
- Problems:
  - global constants freeze env at import time
  - no startup validation for required secrets besides warning for `WORKSHEET_LINK`
- Impact: misconfiguration discovered late and inconsistently
- Minimal diff: add `validate_runtime_config()` used at startup
- Urgency: high

#### [`notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\notification.py:1)
- Role: duplicate notification service at root
- Connected to: currently drift artifact, not main import path
- Problems: duplicate implementation
- Impact: maintainability risk, wrong module import risk
- Minimal diff: replace with re-export or remove
- Urgency: medium

#### [`webhook_followup_handler.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\webhook_followup_handler.py:1)
- Role: patch note artifact for previous manual change
- Connected to: none at runtime
- Problems: dead artifact inside tracked repo
- Impact: confusion about source of truth
- Minimal diff: move to docs/changelog or delete
- Urgency: low

#### [`Readme.md`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\Readme.md:1)
- Role: project documentation
- Connected to: onboarding and deployment
- Problems: still documents v3 structure and `gunicorn app:app`
- Impact: operator confusion, wrong deployment command, wrong mental model
- Minimal diff: update to v4 and factory/scheduler ownership notes
- Urgency: high

### `routes/`

#### [`routes/webhook.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\routes\webhook.py:42)
- Role: Flask endpoint registration and intent dispatch
- Connected to: `utils`, `services`, `services.teleconsult`, `config`
- Problems:
  - route handles validation, orchestration, response composition, and debug exposure
  - imports unused `get_queue_info_message`
  - exposes `NURSE_GROUP_ID` via debug intent
  - logs params as JSON directly which may include PII
- Impact: separation-of-concerns erosion, privacy risk, harder testing
- Minimal diff: extract request parsing/response builders, remove debug route or gate to debug mode, trim logged fields
- Urgency: high

### `services/`

#### [`services/appointment.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\appointment.py:13)
- Role: appointment orchestration
- Connected to: `database`, `services.notification`
- Problems: save + notify + response building in one function
- Impact: synchronous latency and mixed concerns
- Minimal diff: split persistence/notify from message builder
- Urgency: medium

#### [`services/risk_assessment.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\risk_assessment.py:25)
- Role: symptom and personal risk scoring
- Connected to: `database`, `services.notification`, `config`
- Problems: compute + persist + alert in same function
- Impact: harder to unit test and no clean fast-path when external systems fail
- Minimal diff: return structured result object, let caller decide side effects
- Urgency: medium

#### [`services/knowledge.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\knowledge.py:1)
- Role: static health content
- Connected to: route topic map
- Problems: content-only module is fine; issue is content routing logic lives in route not module
- Impact: low
- Minimal diff: add topic registry here to remove route duplication
- Urgency: low

#### [`services/reminder.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\reminder.py:80)
- Role: reminder send, schedule orchestration, response processing, no-response alerts, summary aggregation
- Connected to: `database.reminders`, `services.notification`, `services.scheduler`
- Problems:
  - orchestration + persistence + summary aggregation + alerts mixed together
  - hidden import of `get_scheduled_reminders` hurts testability
  - summary path scans all rows for each user request
- Impact: high latency and fragile tests
- Minimal diff: move summary data access into repository helper and inject dependency at module scope
- Urgency: high

#### [`services/scheduler.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\scheduler.py:33)
- Role: APScheduler bootstrap and job management
- Connected to: `database.reminders`, `services.reminder`
- Problems:
  - in-memory job store
  - tied to app import lifecycle
  - no ownership lock for multi-worker deployments
- Impact: duplicate/missed reminders and operational fragility
- Minimal diff: env-gate startup and move persistent source of truth responsibility out of web workers
- Urgency: critical

#### [`services/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\teleconsult.py:108)
- Role: teleconsult business rules and nurse routing
- Connected to: `database.teleconsult`, `services.notification`, `config`
- Problems:
  - race-prone create/check/queue flow
  - after-hours status mismatch
  - emergency escalation duplicates session trail
  - queue-full and queue-insert failure paths do not cleanly reconcile state
- Impact: user-facing instability and nurse-side confusion
- Minimal diff: add transactional wrapper, unify session lookup semantics, explicit rollback states
- Urgency: critical

#### [`services/notification.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\services\notification.py:18)
- Role: LINE push integration
- Connected to: almost all mutation use cases
- Problems:
  - synchronous call in request path
  - no backoff/circuit breaker
  - uses env-derived token loaded at import time
- Impact: latency spikes and retry storm risk
- Minimal diff: introduce lightweight adapter with retry budget and future async handoff seam
- Urgency: high

### `database/`

#### [`database/sheets.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\sheets.py:29)
- Role: shared Sheets client and core save helpers
- Connected to: all data modules and some services through `database.__init__`
- Problems:
  - caches client only, not spreadsheet/worksheet handles
  - every helper reopens workbook
- Impact: avoidable latency and quota consumption
- Minimal diff: add spreadsheet/worksheet cache
- Urgency: high

#### [`database/reminders.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\reminders.py:22)
- Role: reminder schedule/sent/response/no-response persistence
- Connected to: `services.reminder`, `services.scheduler`
- Problems:
  - heavy full-sheet scans
  - update paths split across multiple API calls
  - status fields rely on header strings everywhere
- Impact: poor scaling and data race exposure
- Minimal diff: repository helpers with batch update and centralized column resolution
- Urgency: critical

#### [`database/teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\database\teleconsult.py:30)
- Role: teleconsult session and queue persistence
- Connected to: `services.teleconsult`
- Problems:
  - no atomic create+queue behavior
  - queue position O(n) and race-prone
  - active-session lookup ignores after-hours state
  - status update uses multiple `update_cell`s
- Impact: correctness, latency, and maintainability issues all at once
- Minimal diff: consolidate row writes, widen lookup semantics, add explicit failure states
- Urgency: critical

### `utils/`

#### [`utils/parsers.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\utils\parsers.py:12)
- Role: date/time/phone normalization
- Connected to: appointment route
- Problems:
  - repeated parsing work done per request; acceptable today
  - broad exception handling remains in some helpers
- Impact: low compared to network I/O
- Minimal diff: precompile regex and reduce exception-based control flow where easy
- Urgency: low

### Tests

#### [`test_bug_fixes.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_bug_fixes.py:1)
- Role: manual regression script
- Connected to: reminder, notification, webhook
- Problems:
  - hardcoded non-repo path
  - emoji output breaks on default Windows console
  - stale patch target for bug 2
- Impact: false negatives and noisy CI/manual validation
- Minimal diff: convert to unittest/pytest and patch correct symbols
- Urgency: high

#### [`test_teleconsult.py`](C:\Kwan's Line Bot\Kwan Nurse Bot\KwanNurse-Bot\test_teleconsult.py:1)
- Role: manual teleconsult smoke test
- Connected to: teleconsult service and DB helpers
- Problems:
  - prints success summary even after key DB failures
  - hardcoded external path
- Impact: misleading signal
- Minimal diff: use assertions and fail-fast behavior
- Urgency: high

## Top 10 Action Items
1. Gate scheduler startup so only one process owns reminder jobs.
2. Fix `after_hours_pending` lookup mismatch between `services/teleconsult.py` and `database/teleconsult.py`.
3. Add rollback state when teleconsult session creation succeeds but queue insertion fails.
4. Batch multi-field Sheets updates and stop using repeated `update_cell()` on hot paths.
5. Cache spreadsheet and worksheet handles, not just the gspread client.
6. Centralize status/header constants for reminder and teleconsult modules.
7. Remove or re-export duplicate root `notification.py` so only one notification implementation exists.
8. Rewrite drifted test scripts to use repo-local imports, real assertions, and UTF-8-safe output.
9. Remove or protect debug route that exposes `NURSE_GROUP_ID`, and reduce PII-rich request logging.
10. Update README and deployment notes to reflect v4 architecture and correct scheduler ownership model.
