# Conversation Flow Isolation and Session Ownership Specification

วันที่: 2026-07-19
สถานะ: Draft for review
ขอบเขต: LINE webhook routing, Dialogflow ES contexts, conversational state, Google Sheets persistence และ Gemini integration

## 1. เป้าหมาย

แยก state ของแต่ละฟีเจอร์ให้ชัดเจน เพื่อให้ข้อความตัวเลขหรือข้อมูลถูกตีความโดยฟีเจอร์ที่กำลังถามอยู่เท่านั้น และป้องกัน stale context, durable business session และข้อความที่มาถึงซ้ำหรือสลับลำดับไม่ให้เปลี่ยน flow ผิดตัว

ฟีเจอร์ที่อยู่ในขอบเขต:

- รายงานอาการ (`reportsymptoms`)
- ประเมินความเสี่ยง (`assessrisk`)
- นัดหมายพยาบาล (`appointment`)
- ปรึกษาพยาบาล/teleconsult (`teleconsult`)
- ลงทะเบียนและคำสั่งที่มี conversational session อื่น ๆ

ไม่อยู่ในขอบเขตระยะแรก:

- การย้ายจาก Dialogflow ES ไป Dialogflow CX
- การเปลี่ยนโครงสร้าง Google Sheets ที่ไม่จำเป็นต่อ routing
- การให้ Gemini เป็นผู้ควบคุม flow
- การเขียนข้อมูล production จริงระหว่าง development/test

## 2. ปัญหาปัจจุบันแบบเข้าใจง่าย

ปัจจุบันมี state อยู่หลายแหล่ง แต่ไม่มีผู้ควบคุมกลางว่า “ตอนนี้ผู้ใช้อยู่ฟีเจอร์ใดและกำลังตอบคำถามข้อใด” ได้แก่ Dialogflow contexts, Flask/session cache และ teleconsult session ที่บันทึกใน Google Sheets

ตัวอย่างปัญหาที่เกิดขึ้น:

1. ผู้ใช้เริ่มรายงานอาการ ระบบถามระดับความปวด และผู้ใช้ตอบ `3`
2. Router เห็นว่า user มี teleconsult session ที่ยัง active
3. Router จึงตีความ `3` เป็น `AfterHoursChoice` แทน pain level
4. ข้อมูลจึงไหลไปฟีเจอร์นัดหมาย/ปรึกษาพยาบาล

ผลที่สังเกตได้จากระบบจริง:

- เลขจากรายงานอาการถูกนำไปเลือกเมนูของ teleconsult
- ข้อมูลอายุ น้ำหนัก และส่วนสูงไหลเข้า risk assessment ผิดช่อง
- ค่า BMI ผิดอย่างรุนแรง เพราะ slot คนละฟีเจอร์ถูกนำมารวมกัน
- Dialogflow context เก่ายังทำงานร่วมกับ context ใหม่
- การเริ่มฟีเจอร์ใหม่ไม่ได้ทำให้ conversational state เดิมสิ้นสุดอย่างเป็นทางการ

ต้นเหตุหลักไม่ใช่การที่ผู้ใช้พิมพ์เลขกำกวม แต่คือระบบใช้ durable teleconsult state เป็น global fallback สำหรับ routing ตัวเลข

## 3. หลักการออกแบบ

### 3.1 One active conversational owner

ผู้ใช้หนึ่งคนมี active interactive flow ได้หนึ่งรายการต่อหนึ่ง conversation channel ในเวลาเดียวกัน ส่วนข้อมูลธุรกิจที่บันทึกถาวร เช่น teleconsult queue สามารถยัง active ได้ แต่ไม่มีสิทธิ์ควบคุมการแปลความหมายของข้อความใหม่

### 3.2 Current step owns the input

ข้อความจะถูกตรวจตาม schema ของ step ปัจจุบันก่อนเสมอ เช่น `reportsymptoms.pain_level` รับเฉพาะจำนวนเต็ม 1-5 ส่วน `assessrisk.weight` รับ decimal ตามช่วงน้ำหนักที่กำหนด

### 3.3 Explicit commands have priority

คำสั่งฟีเจอร์ใหม่และ `ยกเลิก` มีสิทธิ์เปลี่ยนหรือจบ active flow ก่อนการประเมิน slot ปัจจุบัน การเปลี่ยนฟีเจอร์จะทิ้ง slot progress ของ flow เดิม แต่ไม่ยกเลิก durable teleconsult session โดยอัตโนมัติ

### 3.4 External services have bounded responsibilities

- Dialogflow: intent/entity candidate และ context compatibility
- ConversationState: source of truth สำหรับ flow และ step
- Google Sheets: durable business records และ audit
- Gemini: free-text extraction, summarization หรือคำแนะนำที่ไม่ใช่ routing authority
- LINE: transport และ UX prompt/quick reply

## 4. สถาปัตยกรรมที่เสนอ

```text
LINE webhook
    |
    v
Signature + event-id deduplication
    |
    v
Explicit command resolver
    |
    v
ConversationState / FlowRouter  <--- server-side state provider
    |              |
    |              +--> current-step validation
    |
    +--> Dialogflow ES: NLU candidate and context mirror
    |
    +--> deterministic feature handler
                 |
                 +--> Google Sheets: durable record
                 +--> Gemini: optional bounded enrichment
```

ระบบใหม่จะอยู่ใน Flask webhook orchestration layer จึงไม่ต้องเปลี่ยน LINE, Dialogflow agent, Google Sheets หรือ Gemini contract ทั้งหมดในครั้งเดียว

## 5. ConversationState contract

```text
ConversationState {
  user_id: string
  channel_id: string
  flow_id: enum
  flow_instance_id: string
  step_id: string
  allowed_input_type: enum
  allowed_values: optional
  min_value: optional number
  max_value: optional number
  slots: object
  status: active | completed | cancelled | expired
  version: integer
  created_at: timestamp
  updated_at: timestamp
  expires_at: timestamp
  last_event_id: string
}
```

`flow_instance_id` ทำให้ progress จาก flow เก่าไม่สามารถเขียนทับ flow ใหม่ได้ และ `version` ใช้ตรวจ optimistic concurrency

ระยะแรกต้องสร้าง provider interface แยกจาก handler เช่น:

```text
ConversationStateStore.get(user_id, channel_id)
ConversationStateStore.start(state)
ConversationStateStore.compare_and_set(expected_version, next_state)
ConversationStateStore.cancel(flow_instance_id)
```

ห้ามใช้ Google Sheets เป็น store หลักของ state นี้ เพราะ routing ต้องการ read/write ที่เร็วและ atomic ต่อผู้ใช้ ส่วน Sheets คงใช้เป็น durable record ตามเดิม

## 6. Routing precedence

ลำดับการประมวลผลที่บังคับใช้:

1. ตรวจ signature และระบุตัวผู้ใช้
2. ตรวจ `webhookEventId` ซ้ำ
3. ตรวจคำสั่ง `ยกเลิก` และคำสั่งเริ่มฟีเจอร์ใหม่
4. โหลด ConversationState
5. ถ้ามี active state ให้ validate input ตาม `flow_id + step_id`
6. ถ้า input ไม่ตรง schema ให้ตอบขอข้อมูลใหม่ในฟีเจอร์เดิม
7. เรียก Dialogflow เพื่อหา intent เมื่อไม่มี active flow หรือเป็นข้อความอิสระ
8. ตั้ง/ล้าง Dialogflow contexts ให้ตรงกับ active flow
9. บันทึก slot state ด้วย version check
10. เมื่อครบ flow จึงบันทึก durable business record ลง Google Sheets

ข้อห้ามสำคัญ: ห้ามมี global branch ที่ตีความเลข `1-5` จากการพบ teleconsult session เพียงอย่างเดียว

## 7. กติกาแต่ละ integration

### Dialogflow ES

ใช้ context แยก namespace เช่น:

- `reportsymptoms_dialog_context`
- `assessrisk_dialog_context`
- `appointment_dialog_context`
- `teleconsult_dialog_context`

เมื่อเปลี่ยน flow ต้องล้าง contexts ของ flow อื่นที่แข่งขันกัน และต้องไม่เชื่อ intent `AfterHoursChoice` หาก ConversationState ระบุว่า input เป็น slot ของรายงานอาการ

### Google Sheets

ใช้สำหรับ:

- symptom records
- risk profiles
- appointment records
- teleconsult sessions/queue
- audit และ dashboard data

การเขียนต้องใช้ retry ที่มีขอบเขต, idempotency key และตรวจผลสำเร็จจริงก่อนตอบว่าสำเร็จ ห้ามนำ active row ใน Sheets มาเป็นตัวเลือก owner ของตัวเลข

### Gemini API

ใช้เฉพาะ:

- วิเคราะห์ free text ที่ไม่มี deterministic parser
- สรุปอาการหรือคำแนะนำ
- แปลงข้อความธรรมชาติเป็น schema ที่กำหนดไว้

ผลจาก Gemini ต้องผ่าน type/range/business validation อีกครั้ง หาก timeout, `429`, `5xx` หรือ schema ไม่ผ่าน ให้ใช้ fallback ที่ไม่พึ่ง Gemini และห้ามเปลี่ยน active flow

## 8. แผนดำเนินการ

### Phase 0: Hotfix และ regression protection

- ตัด teleconsult database fallback ออกจาก generic numeric routing
- เพิ่ม tests จากเหตุการณ์จริงใน logs
- ยืนยันว่า symptom number ไม่ถูกส่งเข้า `AfterHoursChoice`

### Phase 1: Flow contract foundation

- สร้าง flow/step contract registry
- สร้าง `ConversationState` และ store interface
- สร้าง deterministic input validators
- ย้ายรายงานอาการมาใช้ router กลาง

### Phase 2: Migrate all session features

- ประเมินความเสี่ยง
- นัดหมายพยาบาล
- teleconsult และ after-hours
- registration และคำสั่งที่มี slot state

### Phase 3: Reliability and observability

- webhook deduplication
- optimistic concurrency/version check
- structured routing logs
- metrics และ alerts สำหรับ context mismatch
- restart/multi-worker verification

## 9. Test matrix

ต้องมี automated tests อย่างน้อย:

- เลข `1-5` ในรายงานอาการทุกค่า
- เลขเดียวกันใน risk assessment, appointment และ teleconsult
- มี stale context ของฟีเจอร์อื่น
- มี active teleconsult session แต่ไม่มี teleconsult context
- ส่งคำสั่งฟีเจอร์ใหม่ระหว่าง flow เดิม
- ตอบ `ไม่มี` ใน risk disease slot
- input decimal, date, month, free text และ invalid range
- webhook event ซ้ำ
- webhook event มาถึงสลับลำดับ
- Google Sheets timeout/429
- Gemini timeout/429/invalid structured output
- process restart และหลาย worker

## 10. Exit criteria

จะไม่ประกาศ production ready จนกว่าจะครบทุกข้อ:

- test suite เดิมผ่านโดยไม่ลด coverage
- regression และ flow matrix ผ่าน 100%
- ไม่มี cross-flow routing ใน log/test evidence
- duplicate webhook ไม่สร้างข้อมูลหรือ queue ซ้ำ
- state รุ่นเก่าไม่สามารถ overwrite state รุ่นใหม่
- Sheets และ Gemini failure มี bounded fallback
- routing overhead ภายในแอปไม่เกิน 50 ms โดยไม่รวม external API latency
- มี rollback flag และ runbook
- ผ่าน manual LINE acceptance test จริงใน staging
- มี monitoring สำหรับ routing mismatch, validation failure และ persistence failure

## 11. Rollback plan

เปิด feature flag เพื่อหยุด FlowRouter ใหม่และกลับไปยัง handler ที่มีอยู่ โดยไม่ลบข้อมูลใน Sheets และไม่เปลี่ยน Dialogflow agent ระหว่าง rollout หากพบ cross-flow routing, duplicate persistence หรือ state corruption ให้หยุด rollout, เก็บ correlation logs และปิด flag ก่อนวิเคราะห์ต่อ

## 12. Architecture smells ที่ต้องติดตาม

- การใช้ durable business session เป็น conversational router ทำให้ state คนละอายุและคนละความหมายปะปนกัน
- การพึ่ง process-local cache จะไม่ปลอดภัยเมื่อมีหลาย worker หรือ process restart
- การเรียก external API ในเส้นทางรับ slot โดยตรงเพิ่ม latency และทำให้ failure ภายนอกเปลี่ยนพฤติกรรม flow
- การให้ LLM สกัดข้อมูลโดยไม่มี schema/range validation เปิดช่องให้ข้อมูลผิดชนิดเข้าสู่ risk calculation

## 13. Decision required

เอกสารนี้ขออนุมัติหลักการดังต่อไปนี้:

1. ConversationState เป็น source of truth สำหรับ interactive flow
2. Google Sheets เป็น durable persistence ไม่ใช่ hot session store
3. Dialogflow เป็น NLU/context compatibility layer
4. Gemini เป็น optional enrichment และไม่ใช่ routing authority
5. คำสั่งฟีเจอร์ใหม่เปลี่ยน active flow และยกเลิก slot progress เดิม

หลังอนุมัติเอกสารนี้ ขั้นตอนถัดไปคือจัดทำ implementation plan แบบเป็นลำดับงานและ test-first ก่อนเริ่มแก้โค้ด
