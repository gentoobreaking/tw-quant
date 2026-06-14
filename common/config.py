"""設定檔載入 — 支援 deep merge 外部 JSON 覆蓋預設值"""
import copy
import json
import os


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
