"""各資料源獨立 rate limiter，互不阻塞（thread-safe）"""
import random
import threading
import time


class RateLimiter:
    def __init__(self, config: dict):
        self._cfg = {}
        self._locks = {}
        for src, rl in config.items():
            self._cfg[src] = {"delay": rl["delay"], "jitter": rl["jitter"], "last": 0.0}
            self._locks[src] = threading.Lock()

    def wait(self, source: str):
        cfg = self._cfg.get(source)
        if cfg is None:
            return
        lock = self._locks.get(source)
        if lock is None:
            return
        with lock:
            now = time.time()
            needed = cfg["delay"] + random.uniform(-cfg["jitter"], cfg["jitter"])
            elapsed = now - cfg["last"]
            if elapsed < needed:
                time.sleep(needed - elapsed)
            cfg["last"] = time.time()
