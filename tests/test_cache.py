import json
import os
import tempfile
import time
import sys
sys.path.insert(0, ".")

import pytest
from common.cache import DiskCache


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def cache(db_path):
    c = DiskCache(db_path, ttl=3600)
    yield c
    c.close()


# ═══════════════════════════════════════════════════════════
# DiskCache — 基本 get / TTL
# ═══════════════════════════════════════════════════════════

class TestDiskCacheGet:
    def test_fetch_on_miss(self, cache):
        called = 0
        def fetch():
            nonlocal called
            called += 1
            return 42

        val = cache.get("x", fetch)
        assert val == 42
        assert called == 1

    def test_serves_from_cache(self, cache):
        cache.get("x", lambda: 1)
        called = 0
        def fetch():
            nonlocal called
            called += 1
            return 999

        val = cache.get("x", fetch)
        assert val == 1
        assert called == 0

    def test_ttl_expiry(self, cache):
        cache.get("x", lambda: 1, ttl=0)
        val = cache.get("x", lambda: 999, ttl=0)
        assert val == 999

    def test_custom_ttl_longer(self, cache):
        cache.get("x", lambda: 1, ttl=3600)
        val = cache.get("x", lambda: 999, ttl=3600)
        assert val == 1

    def test_skip_none(self, cache):
        val = cache.get("y", lambda: None, skip_none=True)
        assert val is None
        # 不應快取 None，下次 fetch 仍應被呼叫
        called = 0
        def fetch():
            nonlocal called
            called += 1
            return 42
        assert cache.get("y", fetch, skip_none=True) == 42
        assert called == 1

    def test_serialize_failure_returns_val(self, cache):
        class BadObj:
            pass
        val = cache.get("z", lambda: BadObj())
        assert isinstance(val, BadObj)

    def test_corrupted_cache(self, cache):
        conn = cache._get_conn()
        conn.execute("INSERT OR REPLACE INTO cache (key, data, ts) VALUES (?, ?, ?)",
                      ("corrupt", "not-json{", time.time()))
        conn.commit()
        val = cache.get("corrupt", lambda: 77)
        assert val == 77


# ═══════════════════════════════════════════════════════════
# DiskCache — load_disk_cache / save_disk_cache
# ═══════════════════════════════════════════════════════════

class TestDiskCacheBulk:
    def test_save_and_load(self, cache):
        cache.save_disk_cache({"a": {"data": 1, "_ts": 100},
                                "b": {"data": 2, "_ts": 200}})
        loaded = cache.load_disk_cache()
        assert loaded["a"]["data"] == 1
        assert loaded["b"]["data"] == 2

    def test_save_without_ts(self, cache):
        now = time.time()
        cache.save_disk_cache({"k": {"data": "v"}})
        loaded = cache.load_disk_cache()
        assert loaded["k"]["data"] == "v"
        assert loaded["k"]["_ts"] >= now

    def test_save_plain_value(self, cache):
        cache.save_disk_cache({"x": "plain"})
        loaded = cache.load_disk_cache()
        assert loaded["x"]["data"] == "plain"

    def test_load_empty(self, cache):
        assert cache.load_disk_cache() == {}


# ═══════════════════════════════════════════════════════════
# DiskCache — flush / cleanup
# ═══════════════════════════════════════════════════════════

class TestDiskCacheFlush:
    def test_flush_cleans_old_entries(self, cache):
        cache.ttl = 60
        old = time.time() - 3600
        conn = cache._get_conn()
        conn.execute("INSERT INTO cache (key, data, ts) VALUES (?, ?, ?)",
                      ("old", '"v"', old))
        conn.commit()
        cache._dirty = True
        cache.flush()
        row = conn.execute("SELECT key FROM cache WHERE key='old'").fetchone()
        assert row is None


# ═══════════════════════════════════════════════════════════
# DiskCache — close / reopen
# ═══════════════════════════════════════════════════════════

class TestDiskCachePersistence:
    def test_data_survives_reopen(self, db_path):
        c1 = DiskCache(db_path)
        c1.get("k", lambda: 42)
        c1.close()

        c2 = DiskCache(db_path)
        val = c2.get("k", lambda: 999)
        assert val == 42
        c2.close()


# ═══════════════════════════════════════════════════════════
# DiskCache — 並發寫入安全
# ═══════════════════════════════════════════════════════════

class TestDiskCacheConcurrency:
    def test_concurrent_writes(self, db_path):
        """多執行緒同時寫入不應拋出 OperationalError"""
        from concurrent.futures import ThreadPoolExecutor
        cache = DiskCache(db_path, ttl=3600)
        errors = []

        def write_item(i):
            try:
                cache.get(f"key_{i}", lambda: i * 10)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(write_item, i) for i in range(100)]
            for f in futures:
                f.result()

        cache.close()
        assert errors == [], f"並發寫入錯誤: {errors}"

        # 驗證資料完整
        cache2 = DiskCache(db_path, ttl=3600)
        for i in range(100):
            val = cache2.get(f"key_{i}", lambda: -1)
            assert val == i * 10, f"key_{i} 預期 {i*10}，得到 {val}"
        cache2.close()

    def test_concurrent_read_write(self, db_path):
        """讀寫並發不應崩潰"""
        from concurrent.futures import ThreadPoolExecutor
        cache = DiskCache(db_path, ttl=3600)
        # 先寫入一些資料
        for i in range(20):
            cache.get(f"key_{i}", lambda i=i: i * 10)

        errors = []

        def read_item(i):
            try:
                cache.get(f"key_{i}", lambda: -1)
            except Exception as e:
                errors.append(e)

        def write_item(i):
            try:
                cache.get(f"new_{i}", lambda: i)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for i in range(20):
                futures.append(pool.submit(read_item, i))
            for i in range(50):
                futures.append(pool.submit(write_item, i))
            for f in futures:
                f.result()

        cache.close()
        assert errors == [], f"並發讀寫錯誤: {errors}"
