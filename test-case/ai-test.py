#!/Users/david/Projects/tw-quant/venv/bin/python
"""用管線真實 payload 呼叫 _call_llm（含重試邏輯）
用法: python3 ai-test.py [model] [max_tokens] [retries] [retry_backoff] [retry_delay]
"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

# 測試腳本輕量化：註冊空的 common 套件外殼，跳過 __init__.py 的全量載入
# （避免拉進 yfinance 等重依賴；ai_review 實際只依賴 stdlib＋logger＋requests）
import types
_pkg = types.ModuleType("common")
_pkg.__path__ = [str(PROJECT / "common")]
sys.modules.setdefault("common", _pkg)

import pandas as pd

from common.ai_review import (OUTPUT_CONSTRAINTS, _call_llm,
                              build_context, resolve_ai_config)

MODEL = sys.argv[1] if len(sys.argv) > 1 else None
MAX_TOKENS = int(sys.argv[2]) if len(sys.argv) > 2 else None
RETRIES = int(sys.argv[3]) if len(sys.argv) > 3 else None
BACKOFF = float(sys.argv[4]) if len(sys.argv) > 4 else None
DELAY = float(sys.argv[5]) if len(sys.argv) > 5 else None

cfg = json.load(open(PROJECT / "config_pipeline.json"))
ai_cfg = resolve_ai_config(cfg.get("ai_review", {}))
if MODEL:
    ai_cfg["model"] = MODEL
if MAX_TOKENS:
    ai_cfg["max_tokens"] = MAX_TOKENS
if RETRIES is not None:
    ai_cfg["retries"] = RETRIES
if BACKOFF is not None:
    ai_cfg["retry_backoff"] = BACKOFF
if DELAY is not None:
    ai_cfg["retry_delay"] = DELAY
print("resolved:", {k: v for k, v in ai_cfg.items() if k != "system_prompt"})

df = pd.read_csv(PROJECT / "screening_results/pipeline_20260825_top10.csv")
row = df.iloc[0].to_dict()
ctx = build_context(row)
ctx_json = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
messages = [
    {"role": "system",
     "content": f"{ai_cfg['system_prompt']}\n{OUTPUT_CONSTRAINTS}"},
    {"role": "user",
     "content": "以下是台股數據，請風控覆核：\n" + ctx_json},
]
try:
    text = _call_llm(messages, ai_cfg)
    print("OK:", text[:120])
except Exception as e:  # noqa: BLE001
    print("FAIL:", e)
