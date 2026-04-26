# Dialogflow ES → CX Migration Research

> **Status**: Research-only document (no code changes).
> **Author**: Engineering · 2026-04-26
> **Decision deadline**: Q3/2026 หรือเมื่อ active users > 100 คน — แล้วแต่อันไหนถึงก่อน
> **Scope**: ตัดสินใจว่าจะ migrate KwanNurse-Bot จาก Dialogflow ES ไป Dialogflow CX หรือไม่

---

## 1. Executive Summary

| | |
|---|---|
| **Recommendation** | **Path C (Stay on ES)** จนถึง Q3/2026 หรือ user > 100 คน — แล้วค่อย review ใหม่ |
| **Why** | (1) Google ยังไม่ประกาศ ES end-of-life อย่างเป็นทางการ (2) traffic ปัจจุบันต่ำมาก, ไม่กระทบ cost ทั้ง 2 platform (3) feature CX ที่เกินกว่า ES (multi-turn flow, sentiment) ยังไม่จำเป็นใน roadmap 6 เดือนข้างหน้า |
| **Trigger to migrate** | (a) Google ประกาศ EOL อย่างเป็นทางการ, (b) ต้องการ flow-based state machine สำหรับ multi-step diagnosis, (c) traffic > 50K requests/month, (d) ต้องการ analytics ระดับ enterprise |
| **Estimated migration effort** | 2-3 sprints (parallel project + DNS cutover) — ดู §6 |

---

## 2. Background: Dialogflow ES vs CX

### 2.1 ทำไมมี 2 ตัว?
- **Dialogflow ES (Essentials)** — เปิดตัว 2017, intent-based, simple chatbot, **ฟรีไม่จำกัด** สำหรับ text
- **Dialogflow CX (Customer Experience)** — เปิดตัว 2020, **flow-based state machine**, สำหรับ enterprise contact center, **คิดเงินตาม session**

Google ตำแหน่ง CX = next-gen แต่ ES ยัง active (ไม่มี EOL announcement ณ ตอนเขียน 2026-04)

### 2.2 EOL timeline (ที่รู้ ณ 2026-04)
- **ไม่มี hard deadline** ประกาศบน [release notes](https://cloud.google.com/dialogflow/es/docs/release-notes)
- **Maintenance mode warning**: ES ไม่ได้ feature ใหม่ตั้งแต่ ~2023 (เน้น bug fix)
- **Google PSAs**: แนะนำ "new projects use CX" แต่ไม่บังคับ migrate

> 💡 **Risk**: Google เคย sunset Dialogflow API v1 → v2 (2017-2020, 3 ปี notice). ถ้า ES EOL จะมี notice ≥ 1-2 ปี — ไม่ฉุกเฉิน

---

## 3. Feature Matrix

| Feature | ES | CX | KwanNurse ใช้ไหม? |
|---|---|---|---|
| **Intent matching** | ✅ training phrases | ✅ training phrases + intent re-use across flows | ✅ ใช้หนัก |
| **Entities (Custom)** | ✅ KIND_MAP/LIST/REGEXP | ✅ + composite, fuzzy improved | ✅ KnowledgeTopic, WoundStatus |
| **System entities** | ✅ ครบ | ✅ ครบ + Thai number support ดีกว่า | ✅ |
| **Slot filling** | ✅ basic (parameter prompts) | ✅✅ advanced (form-based, conditional) | ⚠️ ใช้แค่ตอน Teleconsult — basic พอ |
| **Multi-turn context** | ⚠️ contexts (lifespan-based, ดูยาก) | ✅✅ explicit pages + transitions | ❌ ปัจจุบันใช้แค่ FollowUpWith / category choice |
| **Webhook fulfillment** | ✅ ทุก intent | ✅ + per-page tag, fulfillment messages | ✅ ใช้หนัก |
| **Sentiment analysis** | ❌ | ✅ built-in score | ❌ ไม่ใช้ |
| **Analytics dashboard** | ⚠️ basic ใน console | ✅✅ Insights + funnel | ❌ ใช้ metrics ของเราเอง |
| **Multi-language** | ✅ per-agent | ✅ per-flow | ❌ ภาษาไทยอย่างเดียว |
| **Voice (telephony)** | ❌ | ✅ CCAI integration | ❌ ไม่ใช่ contact center |
| **Version control / Git** | ⚠️ ZIP export only | ✅✅ proper version + environment | ✅ ใช้ ZIP ตอนนี้, CX ดีกว่ามาก |
| **A/B testing** | ❌ | ✅ environment-based | ❌ |
| **Voice quality** | basic | studio-grade | ❌ ไม่ใช่ |

**Verdict**: ปัจจุบัน KwanNurse ใช้ ES feature ~30% ของที่มี. CX จะปลดล็อค multi-turn diagnosis + analytics — แต่ยังไม่ blocker

---

## 4. Cost Comparison

### 4.1 ES Pricing
- **Text**: ฟรี **ไม่จำกัด requests** (ภายใต้ rate limit ~600 req/min/agent)
- **Audio**: $0.002/15s ของ STT (เราไม่ใช้)

### 4.2 CX Pricing
- **Text request**: $0.007 ต่อ session (= conversation, ปกติ ~5-10 turns)
- **Audio**: $0.001/15s (ถูกกว่า ES STT)
- **Free tier**: ไม่มี → จ่ายตั้งแต่ request แรก

### 4.3 ประเมินจาก traffic ปัจจุบัน
จาก Render log (sample 24 ชม. 2026-04-26):
- **~10-20 webhook calls/day** (ส่วนใหญ่จาก smoke test ของ developer + UptimeRobot)
- **Production users**: ยังเป็น beta, ~1-2 active users

| Scenario | Sessions/day | ES cost | CX cost (sess × $0.007) |
|---|---|---|---|
| ปัจจุบัน (beta) | ~5 | $0 | $0.035/day = **~$1/month** |
| เป้า 6 เดือน (50 users active) | ~150 | $0 | ~$1/day = **~$30/month** |
| เป้า 1 ปี (200 users active) | ~600 | $0 | ~$4.2/day = **~$130/month** |

**Verdict**: ในช่วง beta cost ต่างกันแค่ ~$1-30/month ไม่ใช่ blocker. ถ้า scale > 500 users → CX จะแพงเร็ว แต่ก็ตามรายได้มาเช่นกัน

---

## 5. Migration Risks & Compatibility

### 5.1 Breaking changes (ต้องเขียน code ใหม่)

| Component | Impact | Effort |
|---|---|---|
| **Webhook payload schema** | CX ใช้ schema ต่างจาก ES (queryResult.parameters → sessionInfo.parameters; intent → match.intent) | 🔴 High — rewrite `routes/webhook.py` ทั้งไฟล์ (~700 LOC) |
| **Intent → Page mapping** | CX ไม่มี "intent" เดี่ยวๆ — intent ผูกกับ page; ต้องออกแบบ flow + page graph ใหม่ | 🔴 High — design 1-2 สัปดาห์ |
| **Context → State** | ES contexts (lifespan=5) → CX state (page transition) | 🟡 Medium |
| **Training phrases** | Portable — copy ได้ + ตรวจ entity refs | 🟢 Low |

### 5.2 Non-breaking (portable)

| Component | Effort |
|---|---|
| **Custom entities** (KnowledgeTopic, WoundStatus, etc.) | 🟢 Low — JSON schema คล้ายกัน, ใช้ `gcloud dialogflow cx entity-types` import ได้ |
| **System entities** | 🟢 ไม่ต้องทำอะไร |
| **Training phrases** | 🟢 Copy + re-annotate |
| **Default fallback / welcome** | 🟢 รื้อแบบ 1:1 ได้ |

### 5.3 Hidden risks
- **Thai NLU regression**: CX อาจ match Thai ได้ต่างจาก ES (training data ต่าง shard) — **ต้อง smoke test 50+ utterances** ทุก intent
- **Session ID convention**: ES ใช้ user-provided session, CX มี session lifetime คุม → user reply ข้าม session อาจ break flow
- **Webhook timeout**: CX strict 5 วินาที (ES ก็ 5s แต่บางทียืดได้); ตอนนี้ Gemini call ของเราอาจ > 5s ในบาง vision case
- **Console UI**: CX UI complex มากกว่า → onboarding พยาบาลทีมต้องสอนใหม่ (ถ้า non-eng จะต้องใช้)
- **Region constraint**: CX ต้องเลือก region ตอน create agent — ต้องเลือก `asia-southeast1` ถ้าอยากให้ latency ต่ำ

---

## 6. Migration Paths

### Path A — Big bang (parallel project + DNS cutover)

```
Week 1-2:  Setup CX project + port entities + design flow graph
Week 3-4:  Port intents → pages + write CX webhook adapter
Week 5:    QA + smoke test 100+ utterances bilingual
Week 6:    DNS cutover (LINE webhook URL → new server) + monitor 48h
Week 7:    Decommission ES agent
```

- **Effort**: 2-3 sprints (~100-150 hrs)
- **Risk**: 🔴 High — full rewrite, ทุก feature ต้อง re-validate
- **Rollback**: ✅ ดี (ES agent ยังอยู่, แค่ flip DNS)

### Path B — Hybrid (ES old, CX new flow only)

- เก็บ ES สำหรับ intent ปัจจุบัน (ReportSymptoms, GetKnowledge, etc.)
- ทำ CX agent แยกสำหรับ flow ใหม่ที่ต้องการ multi-turn (เช่น new "diagnosis wizard")
- Webhook router ดู intent name → ส่งไป handler ของ ES หรือ CX
- **Effort**: 1 sprint (~40-60 hrs)
- **Risk**: 🟡 Medium — 2 codebases ดูแล + 2 ZIP export
- **เหมาะเมื่อ**: มี feature ใหม่ที่ต้องใช้ CX จริงๆ

### Path C — Stay on ES (recommended now)

- ไม่ทำอะไร
- Monitor ES release notes รายเดือน
- Re-evaluate Q3/2026 หรือเมื่อ trigger เกิด (§1)
- **Effort**: 0
- **Risk**: 🟢 Low ระยะสั้น · 🟡 ระยะยาว (ถ้า EOL ประกาศกะทันหัน → emergency Path A)

---

## 7. Recommendation Decision Matrix

| Factor | Score (1-5) | Weight | Note |
|---|---|---|---|
| Active users now | 1 (beta, < 5) | 30% | ไม่ urgent |
| Feature roadmap needs CX-only feature | 2 (S5-S6 plans ใช้ ES feature ได้พอ) | 25% | wound trend, voice STT — ทำใน app code ได้ |
| Cost sensitivity | 3 (ตอนนี้ใช้ free tier) | 15% | scale ขึ้นค่อย worry |
| Team capacity | 2 (เดี่ยว, ทำ phase อื่นอยู่) | 20% | migration ใช้ 2-3 sprint จะ block phase อื่น |
| ES EOL risk | 2 (ไม่มี announcement) | 10% | ติดตาม release notes |
| **Total** | **2.05/5** | | **ไม่ urgent** |

**→ ผ่าน threshold 3.0 เมื่อไหร่ → migrate** (re-evaluate Q3/2026)

---

## 8. Action items (ถ้าตัดสินใจ migrate ในอนาคต)

### Phase 1: Setup (1-2 ชม.)
- [ ] Create CX agent ใน region `asia-southeast1`
- [ ] Setup billing alert ($50/month threshold)
- [ ] Export ES agent ZIP เป็น backup baseline

### Phase 2: Port assets (1 sprint)
- [ ] Port 5 custom entities (KnowledgeTopic, WoundStatus, IssueCategory, AfterHoursChoice, FollowUpAnswer)
- [ ] Re-annotate training phrases
- [ ] Design flow graph + page transitions (Miro/Excalidraw)

### Phase 3: Webhook adapter (1 sprint)
- [ ] Add `routes/cx_webhook.py` แยกจาก ES
- [ ] Map sessionInfo.parameters → existing handler signatures
- [ ] Update LINE webhook config (ถ้ารวม) หรือ run parallel server

### Phase 4: QA + cutover (1 sprint)
- [ ] Smoke test 100+ utterances ทุก intent (Thai + อังกฤษถ้ามี)
- [ ] Latency benchmark (p50/p95/p99)
- [ ] DNS cutover + monitor 48h + rollback plan ready
- [ ] Decommission ES agent (ลบใน month +1)

---

## 9. Sources

- [Dialogflow ES Release Notes](https://cloud.google.com/dialogflow/es/docs/release-notes) — last checked 2026-04-26, no EOL announcement
- [Dialogflow CX Migration Guide](https://cloud.google.com/dialogflow/cx/docs/concept/migration)
- [Dialogflow Pricing](https://cloud.google.com/dialogflow/pricing)
- [ES vs CX Comparison (Google blog 2020)](https://cloud.google.com/blog/products/ai-machine-learning/announcing-dialogflow-cx)
- Internal: `dialogflow/` agent ZIP (current ES agent), `routes/webhook.py` (current handlers)

---

## 10. Open questions (review เมื่อ trigger เกิด)

1. ตอน CX migration จะรวม Voice/audio support (S5-2) ใน scope หรือทำหลัง?
2. CX มี Thai-specific NLU model ที่ดีกว่า ES จริงไหม? (ต้อง POC + benchmark)
3. ถ้า user > 1000 active → cost CX อาจสูง > revenue → ทำ self-hosted alternative (Rasa)?
4. ถ้าทีมโต > 2 คน → CX collaboration features คุ้มกว่า ES ZIP-based workflow ไหม?

---

> **Next review date**: 2026-09-01 (Q3 review)
> **Trigger to revisit early**: Google ประกาศ ES EOL · KwanNurse active users > 100 · feature ที่ต้อง CX-only
