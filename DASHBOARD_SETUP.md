# Nurse Dashboard — Setup Guide

คู่มือติดตั้งและใช้งาน **Nurse Dashboard** (Phase 3 Sprint 1) สำหรับพยาบาลดู
คิวปรึกษา · การแจ้งเตือนความเสี่ยง · ประวัติผู้ป่วย ผ่านหน้าเว็บที่ผูกกับ
KwanNurse-Bot.

> เส้นทาง: `/dashboard/*` บน Flask app เดิม (ไม่ต้องรัน process แยก)

---

## 1. ภาพรวมสถาปัตยกรรม

```
Browser (พยาบาล)
   │  HTTPS + session cookie (HttpOnly, SameSite=Lax)
   ▼
Flask app (app.py)
   ├── blueprint: routes/dashboard
   │     ├── auth_views.py   → /dashboard/login, /dashboard/logout
   │     └── views.py         → /dashboard/, /queue, /alerts, /patient/<id>,
   │                            /partials/*, /queue/<id>/assign|complete,
   │                            /alerts/dismiss
   ├── services/auth.py       → bcrypt verify, CSRF, rate limit, idle timeout,
   │                            password policy
   ├── services/dashboard_readers.py  → cache-aware reads (TTL 10–30s)
   └── services/dashboard_actions.py  → write actions + cache invalidate
           │
           ▼
   Google Sheets (TeleconsultQueue, TeleconsultSessions, SymptomLog)
```

**Session storage**: Flask cookie-based session (encrypted ด้วย `FLASK_SECRET_KEY`).
ไม่มี DB ฝั่งเซิร์ฟเวอร์ — เหมาะกับ 1 worker. ถ้าขยายเป็น multi-worker ต้อง
ย้ายไป Redis/server-side session.

---

## 2. ขั้นตอนติดตั้งแบบย่อ

```bash
# 1. สร้าง bcrypt hash ของรหัสผ่านพยาบาลแต่ละคน (ทำแยกทีละคน)
python scripts/make_nurse_hash.py nurse_kwan 'MyStrongPass1234'
# → nurse_kwan:$2b$12$AbC...xyz

python scripts/make_nurse_hash.py nurse_bee 'BeePass2024Safe'
# → nurse_bee:$2b$12$DeF...uvw

# 2. ตั้ง env vars (ดูรายการใน §3) แล้ว deploy/restart
```

Login: เปิด `https://<your-host>/dashboard/login` → กรอก username + password

---

## 3. Environment Variables

### 3.1 จำเป็น (ถ้าไม่ตั้ง → dashboard ถูก disable)

| Variable | ตัวอย่าง | คำอธิบาย |
|---|---|---|
| `NURSE_DASHBOARD_AUTH` | `nurse_kwan:$2b$12$AbC...,nurse_bee:$2b$12$DeF...` | รายชื่อพยาบาลคั่นด้วย `,` รูปแบบ `username:bcrypt_hash` ต่อคน. ถ้าว่าง/ไม่ตั้ง → endpoint `/dashboard/*` ทั้งหมดจะถูกปิด (dashboard disabled). |
| `FLASK_SECRET_KEY` | `a-random-64-byte-hex` | ใช้ sign session cookie. **ถ้าเปลี่ยนค่านี้ → พยาบาลทุกคนจะถูก logout**. สุ่มด้วย `python -c "import secrets; print(secrets.token_hex(32))"` |

### 3.2 ตัวเลือก (มีค่า default ที่ปลอดภัยแล้ว)

| Variable | Default | คำอธิบาย |
|---|---|---|
| `NURSE_DASHBOARD_IDLE_MINUTES` | `15` | หลังไม่มี activity กี่นาทีจะถูกบังคับ logout. ตั้งต่ำถ้ากังวลเรื่องวางมือถือทิ้ง. |
| `FLASK_DEBUG` / `DEBUG` | `false` | ถ้า `true` cookie จะไม่บังคับ `Secure` (ใช้ได้บน `http://localhost`). **ห้ามเปิดบน production**. |

### 3.3 ค่าที่ hardcode (ยังไม่ทำเป็น env var)

- **Login rate limit**: 5 ครั้งผิดต่อ IP ใน 5 นาที → block 403.
- **CSRF token lifetime**: ผูกกับ session (หมดเมื่อ logout).
- **Cache TTL**: queue 10s, alerts 30s, stats 15s, patient timeline 30s,
  dismissed alerts 24h.

หากต้องปรับ ให้แก้ใน `services/auth.py` และ `services/dashboard_readers.py`.

---

## 4. สร้าง Password Hash (Password Policy)

```bash
python scripts/make_nurse_hash.py <username> '<password>'
```

### นโยบายรหัสผ่าน (บังคับโดย `services/auth.py::validate_nurse_password`)

- ยาว **≥ 10** ตัวอักษร, ≤ 72 bytes (UTF-8 — bcrypt limit)
- ต้องมี **ตัวพิมพ์ใหญ่** + **ตัวพิมพ์เล็ก** + **ตัวเลข** อย่างน้อยประเภทละ 1 ตัว
- ห้ามมี username อยู่ภายใน (case-insensitive, ≥4 chars)
- ห้ามเป็น common password (เช่น `Password123`, `qwerty123`, `nurse1234`)

### Exit codes

| Code | ความหมาย |
|---|---|
| 0 | สำเร็จ — print `username:bcrypt_hash` บน stdout |
| 2 | argument ไม่ครบ หรือ username มีอักขระต้องห้าม (`:`, `,`) |
| 3 | รหัสผ่านไม่ผ่าน policy (รายการปัญหา print บน stderr) |

### Bypass (ใช้เฉพาะ dev/test)

```bash
python scripts/make_nurse_hash.py nurse_dev 'weak' --force
```

### รวมหลายคนเป็น env var เดียว

```bash
# bash / zsh
export NURSE_DASHBOARD_AUTH="$(python scripts/make_nurse_hash.py nurse_kwan 'Pass1234Abc'),$(python scripts/make_nurse_hash.py nurse_bee 'Pass5678Xyz')"
```

```pwsh
# PowerShell
$h1 = (python scripts\make_nurse_hash.py nurse_kwan 'Pass1234Abc').Trim()
$h2 = (python scripts\make_nurse_hash.py nurse_bee  'Pass5678Xyz').Trim()
$env:NURSE_DASHBOARD_AUTH = "$h1,$h2"
```

### บน Render (production)

Render → Service → **Environment** → Add:

| Key | Value |
|---|---|
| `NURSE_DASHBOARD_AUTH` | `nurse_kwan:$2b$12$...,nurse_bee:$2b$12$...` |
| `FLASK_SECRET_KEY` | (ค่าที่สุ่ม 64 hex) |

> ⚠️ **ห้าม** commit hash หรือ `FLASK_SECRET_KEY` ลงใน git. ใช้ Render secret store เท่านั้น.

---

## 5. Login Flow & Security Controls

### 5.1 Flow ปกติ

```
1. GET  /dashboard/login            → หน้า login + CSRF token ใน form
2. POST /dashboard/login            → verify bcrypt + rate limit
                                        ├─ ผิด → 401 (บวก failure counter)
                                        └─ ถูก → session.clear() + set user
                                                + generate CSRF ใหม่
                                                + 302 → /dashboard/
3. GET  /dashboard/                 → require_nurse_auth OK → render home
4. ทุก request ต่อไป
   ├─ เช็ค idle timeout (15 นาที default)
   ├─ POST ต้องมี csrf_token ตรงกับ session
   └─ update last_active timestamp
5. POST /dashboard/logout           → session.clear() → redirect login
```

### 5.2 Security matrix

| ภัยคุกคาม | การป้องกัน |
|---|---|
| Password brute-force | bcrypt cost 12 (~150ms/verify) + rate limit 5/5min/IP |
| Session hijacking | Cookie `HttpOnly` + `Secure` (prod) + `SameSite=Lax` |
| CSRF | Token ใน session ตรวจทุก POST (login/logout/assign/complete/dismiss) |
| XSS | Jinja auto-escape + ไม่ใส่ user input ใน `|safe` ที่ไหน |
| Open redirect | `_safe_next_url` รับเฉพาะ path เริ่มด้วย `/` ไม่ใช่ `//` |
| Idle session | ตรวจ `last_active` — เกิน 15 นาที → logout อัตโนมัติ |
| Session fixation | `session.clear()` ก่อน set user + generate CSRF ใหม่ |
| bcrypt silent truncation | Password policy block ≤ 72 bytes ตอนสร้าง hash |

### 5.3 Audit log

ทุก write action บันทึกที่ระดับ `INFO` ใน logger `services.dashboard_actions`:

```
audit: nurse=nurse_kwan action=assign queue_id=q_abc session_id=s_123
audit: nurse=nurse_kwan action=complete queue_id=q_abc session_id=s_123 notes_len=42
audit: nurse=nurse_kwan action=dismiss_alert user_id=Uxxx timestamp=2026-04-24T10:00:00
```

Login events:

```
dashboard login_success user=nurse_kwan
dashboard login fail user=nurse_kwan ip=x.x.x.x
```

---

## 6. Features & URL Map

| URL | Method | คำอธิบาย |
|---|---|---|
| `/dashboard/login` | GET, POST | หน้า login + submit |
| `/dashboard/logout` | POST | ออกจากระบบ (ต้อง CSRF) |
| `/dashboard/` | GET | หน้าหลัก — stats + preview queue/alerts |
| `/dashboard/queue` | GET | ตารางคิวปรึกษาเต็ม (HTMX auto-refresh 15s) |
| `/dashboard/alerts?days=&level=` | GET | รายการแจ้งเตือนกรองตาม days (1–30) และ level (low/medium/high) |
| `/dashboard/patient/<user_id>` | GET | Timeline ผู้ป่วย 1 คน (symptoms + sessions) |
| `/dashboard/queue/<queue_id>/assign` | POST | รับคิว → set `in_progress` + assigned_nurse |
| `/dashboard/queue/<queue_id>/complete` | POST | ปิดเคส → set `completed` + notes (≤500 chars) |
| `/dashboard/alerts/dismiss` | POST | ซ่อน alert 24h (เก็บ in-memory) |
| `/dashboard/partials/queue` | GET | HTMX fragment |
| `/dashboard/partials/alerts` | GET | HTMX fragment |
| `/dashboard/partials/bell` | GET | HTMX fragment (badge นับคิวด่วน + alerts วันนี้, poll 30s) |

---

## 7. Local Development

```bash
# 1. สร้าง .env (ไม่ commit)
FLASK_SECRET_KEY=dev-only-$(python -c "import secrets;print(secrets.token_hex(16))")
NURSE_DASHBOARD_AUTH=nurse_dev:$(python scripts/make_nurse_hash.py nurse_dev 'DevPass1234' | sed 's/^nurse_dev://')
DEBUG=true
RUN_SCHEDULER=false

# 2. รัน
python app.py

# 3. เปิดเบราว์เซอร์
#    http://localhost:5000/dashboard/login
```

### ทดสอบ

```bash
# Unit + view tests เฉพาะ dashboard
python -m unittest test_dashboard_auth.py test_dashboard_readers.py \
                     test_dashboard_actions.py test_dashboard_polish.py -v

# Regression ทั้งหมด
python run_regression_tests.py
```

---

## 8. Troubleshooting

| อาการ | สาเหตุ / แก้ไข |
|---|---|
| `/dashboard/*` → 404 | `NURSE_DASHBOARD_AUTH` ว่าง → dashboard ถูกปิด. ตั้ง env var แล้ว restart. |
| Login สำเร็จแต่ bounce กลับ login ทันที | `FLASK_SECRET_KEY` เปลี่ยนไปจากตอนตั้ง cookie, หรือ `SESSION_COOKIE_SECURE=True` แต่เปิดผ่าน `http://`. ตั้ง `DEBUG=true` ชั่วคราวใน local. |
| `400 CSRF token invalid` | Session หมด (idle timeout), หรือ form เก่า. Refresh หน้าเพื่อรับ token ใหม่. |
| `403 Too many attempts` | Login ผิดเกิน 5 ครั้ง/5 นาที. รอ 5 นาที หรือ restart process ใน dev. |
| Bell badge ไม่อัพเดต | HTMX ไม่โหลด (ดู browser DevTools → Network). CDN block? ใช้ self-hosted HTMX แทน. |
| Password hash ถูก reject | รหัสผ่านไม่ผ่าน policy — ดู §4. ใช้ `--force` ชั่วคราวได้ใน dev เท่านั้น. |

---

## 9. Known Limitations & Future Work

**Sprint 1 scope (ตอนนี้):**
- ✅ Read/write cycle: queue → assign → complete
- ✅ Alerts view + dismiss (in-memory 24h)
- ✅ Patient timeline (symptoms + sessions)
- ✅ Auth + CSRF + rate limit + password policy
- ✅ Mobile responsive + bell notifications

**ยังไม่ได้ทำ (future sprint):**
- ⏳ Server-side session store (Redis) — จำเป็นถ้าขยายเป็น multi-worker
- ⏳ Alert dismissal แบบถาวร (ตอนนี้ process restart → dismissal หาย)
- ⏳ Self-service password change ใน UI
- ⏳ 2FA / TOTP
- ⏳ Audit log export / SIEM integration
- ⏳ Real-time push (Server-Sent Events) แทน 30s polling

---

## 10. อ้างอิงไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
|---|---|
| `services/auth.py` | bcrypt verify, session, CSRF, rate limit, password policy |
| `services/dashboard_readers.py` | Cache-aware readers (queue/alerts/stats/patient) |
| `services/dashboard_actions.py` | Write actions + cache invalidate + audit log |
| `services/cache.py` | In-memory TTL cache (`ttl_cache` singleton) |
| `routes/dashboard/auth_views.py` | Login / logout routes |
| `routes/dashboard/views.py` | Home / queue / alerts / patient / actions / partials |
| `routes/dashboard/templates/` | Jinja2 templates (Tailwind + HTMX) |
| `scripts/make_nurse_hash.py` | CLI สร้าง bcrypt hash |

---

_Updated: Sprint 1 S1-4 complete (Phase 3)_
