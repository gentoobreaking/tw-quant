"""磁碟快取 — SQLite 取代單一 JSON，解決 80MB+ 載入慢與閉包陷阱"""
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .serialization import to_json_val

logger = logging.getLogger(__name__)


class DiskCache:
    """SQLite-backed 磁碟快取，具 TTL 過期機制

    改善重點：
    - 取代單一 JSON（曾超 80MB，json.load 需 3-5 秒）
    - SQLite 逐 key 存取，避免全量讀寫
    - 無 lambda 閉包陷阱（延遲寫入由 flush 控制）
    - 自動清理過期條目
    - 執行緒安全：寫入操作由 _write_lock 保護
    """

    def __init__(self, db_path: str, ttl: int = 7200):
        self.db_path = db_path
        self.ttl = ttl
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()  # 保護所有寫入操作
        self._dirty = False  # 追蹤是否有未 flush 的寫入
        self._ensure_table()

    def _ensure_table(self):
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT,
                    ts REAL
                )
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """取得當前執行緒的 SQLite 連線

        每個執行緒使用獨立連線（SQLite 連線非執行緒安全），
        寫入操作由 _write_lock 保護避免鎖表。
        斷線時自動重連，最多重試 3 次。
        """
        import threading
        tid = threading.get_ident()
        if not hasattr(self, '_conns'):
            self._conns = {}
        for attempt in range(3):
            try:
                conn = self._conns.get(tid)
                if conn is not None:
                    try:
                        conn.execute("SELECT 1")
                    except sqlite3.ProgrammingError:
                        conn = None
                if conn is None:
                    conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=10000")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA cache_size=-8000")
                    self._conns[tid] = conn
                return conn
            except sqlite3.OperationalError:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    raise

    def get(self, key: str, fetch_fn: Callable, ttl: Optional[int] = None,
            skip_none: bool = False):
        """取得快取值，未命中或過期則執行 fetch_fn"""
        now = time.time()
        effective_ttl = ttl if ttl is not None else self.ttl
        conn = self._get_conn()

        row = conn.execute("SELECT data, ts FROM cache WHERE key = ?", (key,)).fetchone()
        if row:
            data, ts = row
            if now - ts < effective_ttl:
                try:
                    return json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    pass  # 損壞的快取，重新抓取

        # 未命中或過期 — fetch_fn 不持鎖（可能很慢）
        val = fetch_fn()
        if val is None and skip_none:
            return None

        try:
            serialized = json.dumps(val, default=to_json_val)
            with self._write_lock:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, data, ts) VALUES (?, ?, ?)",
                    (key, serialized, now)
                )
                conn.commit()
            self._dirty = True
        except (TypeError, ValueError) as e:
            # 序列化失敗，仍回傳值但不快取
            pass
        except sqlite3.OperationalError as e:
            # SQLite 鎖定失敗（罕見，Lock 已保護大部分情況）
            logger.warning("快取寫入失敗 key=%s: %s", key, e)

        return val

    def flush(self):
        """強制寫入（SQLite 已 auto-commit，此處清理過期條目）"""
        if self._dirty:
            self._cleanup_expired()
            self._dirty = False

    def _cleanup_expired(self):
        """清理過期條目，避免 DB 無限膨脹"""
        cutoff = time.time() - self.ttl * 2  # 保留 2 倍 TTL 的緩衝
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM cache WHERE ts < ?", (cutoff,))
            conn.commit()

    def close(self):
        """關閉所有執行緒的連線"""
        if hasattr(self, '_conns'):
            for conn in self._conns.values():
                if conn:
                    conn.close()
            self._conns.clear()

    # ---- 相容舊介面 ----
    def load_disk_cache(self) -> dict:
        """相容舊 _load_disk_cache — 回傳全部快取的 dict view"""
        conn = self._get_conn()
        result = {}
        for key, data, ts in conn.execute("SELECT key, data, ts FROM cache"):
            try:
                result[key] = {"data": json.loads(data), "_ts": ts}
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def save_disk_cache(self, cache: dict):
        """相容舊 _save_disk_cache — 批次寫入 dict"""
        now = time.time()
        with self._write_lock:
            conn = self._get_conn()
            for key, entry in cache.items():
                data = entry.get("data") if isinstance(entry, dict) else entry
                ts = entry.get("_ts", now) if isinstance(entry, dict) else now
                try:
                    serialized = json.dumps(data, default=to_json_val)
                    conn.execute(
                        "INSERT OR REPLACE INTO cache (key, data, ts) VALUES (?, ?, ?)",
                        (key, serialized, ts)
                    )
                except (TypeError, ValueError):
                    pass
            conn.commit()
        self._dirty = True
