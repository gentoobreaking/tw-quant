#!/Users/david/Projects/tw-quant/venv/bin/python
"""對照實驗：簡短問題 vs 管線 payload，觀察 reasoning 是否吃光 max_tokens
用法: python3 ai-test2.py [model]
"""
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

# 測試腳本輕量化：跳過 common/__init__.py 全量載入（避免 yfinance 重依賴）
import types
_pkg = types.ModuleType("common")
_pkg.__path__ = [str(PROJECT / "common")]
sys.modules.setdefault("common", _pkg)

import pandas as pd
import requests

from common.ai_review import (OUTPUT_CONSTRAINTS, build_context,
                              resolve_ai_config)

MODEL = sys.argv[1] if len(sys.argv) > 1 else "x-preview-f-free"

ai_cfg = resolve_ai_config(
    json.load(open(PROJECT / "config_pipeline.json"))["ai_review"])
url = ai_cfg["base_url"] + "/chat/completions"
headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}


def call(messages, max_tokens):
    payload = {"model": MODEL, "messages": messages,
               "max_tokens": max_tokens}
    for i in range(4):
        try:
            r = requests.post(url, json=payload, headers=headers,
                              timeout=3600)
        except Exception as e:  # noqa: BLE001
            print("  conn err:", str(e)[:60])
            time.sleep(5)
            continue
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}，重試…")
            time.sleep(5)
            continue
        j = r.json()
        ch = j["choices"][0]
        m = ch["message"]
        return {"finish_reason": ch["finish_reason"],
                "content_len": len(m.get("content") or ""),
                "reasoning_len": len(m.get("reasoning_content") or ""),
                "completion_tokens":
                    j.get("usage", {}).get("completion_tokens")}
    return None


# A：簡短問題
short = [{"role": "user", "content": "你好，請用一句話介紹台積電"}]
print(f"A. 簡短問題   max_tokens=131072 :", call(short, 300))

# B：管線真實 payload（大 JSON＋system prompt，max_tokens=1200）
df = pd.read_csv(PROJECT / "screening_results/pipeline_20260825_top10.csv")
ctx = build_context(df.iloc[0].to_dict())
ctx_json = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
pipeline = [
    {"role": "system",
     "content": f"{ai_cfg['system_prompt']}\n{OUTPUT_CONSTRAINTS}"},
    {"role": "user",
     "content": "以下是台股數據，請風控覆核：\n" + ctx_json},
]
print(f"B. 管線payload max_tokens=131072:", call(pipeline, 131072))
