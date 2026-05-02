# -*- coding: utf-8 -*-
"""
Phase 3 Sprint 1 (S1-1): ทดสอบ TTL cache ใน ``services/cache.py``.

ขอบเขต:
- set/get ค่าปกติ
- หมดอายุแล้วต้องคืน None
- invalidate ทีละ key และทีละ prefix
- clear ล้างทั้งหมด

Run::

    python -m unittest test_cache.py -v
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TTLCacheTests(unittest.TestCase):

    def setUp(self):
        from services.cache import ttl_cache
        ttl_cache.clear()

    def test_set_and_get_returns_value(self):
        from services.cache import ttl_cache
        ttl_cache.set("k1", {"hello": "world"}, ttl_seconds=10)
        self.assertEqual(ttl_cache.get("k1"), {"hello": "world"})

    def test_get_missing_key_returns_none(self):
        from services.cache import ttl_cache
        self.assertIsNone(ttl_cache.get("nonexistent"))

    def test_expired_entry_returns_none(self):
        from services.cache import ttl_cache
        ttl_cache.set("k1", "value", ttl_seconds=0.05)
        time.sleep(0.1)
        self.assertIsNone(ttl_cache.get("k1"))
        # entry หายจาก store ด้วย (self-cleaning)
        self.assertEqual(ttl_cache.size(), 0)

    def test_invalidate_removes_key(self):
        from services.cache import ttl_cache
        ttl_cache.set("k1", 1, ttl_seconds=10)
        ttl_cache.invalidate("k1")
        self.assertIsNone(ttl_cache.get("k1"))

    def test_invalidate_prefix_removes_matching_keys(self):
        from services.cache import ttl_cache
        ttl_cache.set("dash:queue:a", 1, ttl_seconds=10)
        ttl_cache.set("dash:queue:b", 2, ttl_seconds=10)
        ttl_cache.set("dash:alerts:x", 3, ttl_seconds=10)
        removed = ttl_cache.invalidate_prefix("dash:queue:")
        self.assertEqual(removed, 2)
        self.assertIsNone(ttl_cache.get("dash:queue:a"))
        self.assertIsNone(ttl_cache.get("dash:queue:b"))
        # อีก namespace ไม่ถูกกระทบ
        self.assertEqual(ttl_cache.get("dash:alerts:x"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
