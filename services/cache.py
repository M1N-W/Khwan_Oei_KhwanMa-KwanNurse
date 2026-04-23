# -*- coding: utf-8 -*-
"""
ระบบ cache แบบ TTL (Time-To-Live) สำหรับใช้ภายใน process เดียว.

จุดประสงค์:
- ลดภาระการเรียก Google Sheets API ซ้ำซ้อน เมื่อพยาบาลหลายคนเปิด dashboard
  ในเวลาใกล้เคียงกัน (เช่น queue view, alerts view).
- ใช้ thread-safe เพราะ Flask จัดการ request พร้อมกันได้หลาย thread
  (แม้ Gunicorn จะใช้ worker เดียวใน free tier).

การออกแบบ:
- ทำง่ายที่สุด: dict + threading.Lock. ไม่มี dependency นอก stdlib.
- ค่าที่เก็บมี ``expires_at`` (epoch seconds). เมื่อเรียก ``get`` เจอค่าเก่า
  (ผ่าน TTL แล้ว) จะลบทิ้งและคืน ``None`` — ทำให้ไม่ต้องมี background
  cleanup thread.
- Reset เมื่อ process restart (Render restart บ่อยใน free tier — ยอมรับได้
  เพราะเป็น non-critical cache, เสียหายสุดคือยิง Sheets ซ้ำรอบเดียว).

วิธีใช้::

    from services.cache import ttl_cache

    data = ttl_cache.get("dash:queue:limit=50")
    if data is None:
        data = _load_queue_from_sheets(limit=50)
        ttl_cache.set("dash:queue:limit=50", data, ttl_seconds=10)

การตั้งชื่อ key แนะนำ: ``"{namespace}:{reader}:{args_hash}"`` เช่น
``"dash:queue:limit=50"`` เพื่อให้ invalidate เป็นกลุ่มได้ง่าย.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class _TTLCache:
    """Cache ในหน่วยความจำที่ entry แต่ละตัวมีอายุจำกัด (TTL)."""

    def __init__(self) -> None:
        # โครงสร้าง: {key: (value, expires_at_epoch)}
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """ดึงค่าจาก cache. คืน ``None`` ถ้าหมดอายุหรือไม่มี key."""
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at <= now:
                # หมดอายุแล้ว — ลบทิ้งเลยเพื่อไม่ให้กิน memory
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """ตั้งค่าพร้อม TTL. ``ttl_seconds`` ควร > 0."""
        if ttl_seconds <= 0:
            return
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (value, expires_at)

    def invalidate(self, key: str) -> None:
        """ลบ entry เฉพาะ key (เรียกเมื่อมีการเขียนข้อมูลใหม่ที่ cache เก่า stale แล้ว)."""
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """ลบทุก entry ที่ key ขึ้นต้นด้วย ``prefix``. คืนจำนวนที่ลบไป."""
        with self._lock:
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                self._store.pop(k, None)
            return len(keys_to_remove)

    def clear(self) -> None:
        """ลบทุก entry (ใช้ใน test เป็นหลัก)."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """จำนวน entry ปัจจุบัน (นับทั้งที่หมดอายุแล้วแต่ยังไม่ถูกล้าง)."""
        with self._lock:
            return len(self._store)


# Instance กลางสำหรับทั้งแอป — ใช้ผ่าน import นี้เสมอ
ttl_cache = _TTLCache()
