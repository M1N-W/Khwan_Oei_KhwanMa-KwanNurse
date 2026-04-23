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

import bcrypt


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        print("ต้องระบุ username และ password เป็น argument", file=sys.stderr)
        return 2

    username = sys.argv[1].strip()
    password = sys.argv[2]

    if not username or ":" in username or "," in username:
        print("Username (ชื่อผู้ใช้) ต้องไม่ว่างและห้ามมี ':' หรือ ','", file=sys.stderr)
        return 2

    if len(password) < 8:
        print("รหัสผ่านควรยาวอย่างน้อย 8 ตัวอักษร", file=sys.stderr)

    # cost 12 = ช้าพอที่จะกัน brute force แต่ไม่นานเกินไปสำหรับ login (<200ms)
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    print(f"{username}:{hashed.decode('utf-8')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
