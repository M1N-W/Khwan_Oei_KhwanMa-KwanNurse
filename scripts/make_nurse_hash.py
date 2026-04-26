# -*- coding: utf-8 -*-
"""
Helper สำหรับสร้าง bcrypt hash ที่ใช้ใน env var ``NURSE_DASHBOARD_AUTH``.

วิธีใช้::

    python scripts/make_nurse_hash.py <username> <password>

ตัวอย่าง::

    python scripts/make_nurse_hash.py nurse_kwan 'MyStrongPass123!'

ผลลัพธ์คือ 1 บรรทัดที่ copy ไปวางใน env var ได้ทันที เช่น::

    nurse_kwan:$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW

รวมหลายคนด้วยเครื่องหมาย ``,``::

    nurse_kwan:$2b$12$...,nurse_bee:$2b$12$...
"""
from __future__ import annotations

import sys
from pathlib import Path

import bcrypt

# Allow running as ``python scripts/make_nurse_hash.py`` — เพิ่ม project root
# ใน sys.path เพื่อให้ import ``services.auth`` ได้โดยไม่ต้องตั้ง PYTHONPATH
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from services.auth import validate_nurse_password  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv

    if len(args) != 2:
        print(__doc__)
        print("ต้องระบุ username และ password เป็น argument", file=sys.stderr)
        print("เพิ่ม --force เพื่อบังคับสร้าง hash ทั้งที่รหัสผ่านไม่ตรงนโยบาย (ไม่แนะนำ)",
              file=sys.stderr)
        return 2

    username = args[0].strip()
    password = args[1]

    if not username or ":" in username or "," in username:
        print("Username (ชื่อผู้ใช้) ต้องไม่ว่างและห้ามมี ':' หรือ ','", file=sys.stderr)
        return 2

    # บังคับ password policy (S1-4). ถ้าไม่ผ่าน → exit 3 (ยกเว้น --force)
    issues = validate_nurse_password(password, username=username)
    if issues:
        print("รหัสผ่านไม่ผ่านนโยบาย:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        if not force:
            print("\nแก้รหัสผ่านหรือใส่ --force เพื่อ bypass (ไม่แนะนำ)", file=sys.stderr)
            return 3
        print("  [WARN] ข้าม policy ด้วย --force — ควรใช้เฉพาะ dev/test", file=sys.stderr)

    # cost 12 = ช้าพอที่จะกัน brute force แต่ไม่นานเกินไปสำหรับ login (<200ms)
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    print(f"{username}:{hashed.decode('utf-8')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
