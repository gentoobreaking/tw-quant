"""設定檔載入 — 支援 deep merge 外部 JSON 覆蓋預設值"""

import copy
import json
import os
from typing import Optional


def deep_merge(base: dict, override: dict) -> dict:
    """遞迴合併 override 進 base（in-place），回傳 base"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: str, defaults: dict) -> dict:
    """載入設定檔，若外部 JSON 存在則 deep merge 覆蓋預設值"""
    cfg = copy.deepcopy(defaults)
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                loaded = json.load(f)
                return deep_merge(cfg, loaded)
        except Exception:
            pass
    return cfg


def get_database_url() -> Optional[str]:
    """取得 PostgreSQL 連線字串 (DATABASE_URL 環境變數)"""
    return os.environ.get("DATABASE_URL")


def get_cache_config(cfg: dict) -> dict:
    """從設定中取得快取配置"""
    cache_cfg = cfg.get("cache", {})
    db_url = cache_cfg.get("database_url") or os.environ.get("DATABASE_URL")
    ttl = cache_cfg.get("ttl_seconds", 7200)
    return {"database_url": db_url, "ttl": ttl}
