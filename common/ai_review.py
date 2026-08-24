"""AI 評估模組（T014 增補）——Top5 買點清單的質性覆核

設計原則：
- 量化分級是決策主體，AI 只補充 ≤100 字的質性觀察，不得更改分級
- OpenAI 相容端點：雲端 API 與本地 Qwen（Ollama/LM Studio/vLLM）通吃
- 優先序：OPENAI_BASE_URL / OPENAI_API_KEY 環境變數 > config ai_review 區塊
- 快取：以「資料內容＋模型」雜湊為 key，數據沒變不重複呼叫
- 失敗容錯：任何錯誤回傳「—」開頭的說明，不中斷管線
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

import requests

from .logger import logger

# 使用者自訂的角色設定（config 可覆蓋）＋ 固定的輸出約束
DEFAULT_SYSTEM_PROMPT = (
    "你現在是一名資深台股量化投資分析師，"
    "拿到數據不是只是看數據給結論，"
    "而是要重新收集相關數據及校驗數據及分析市場後，"
    "給出真實的台股量化投資建議。"
)

# 疊加在角色設定後的輸出約束（程式固定，不可被 prompt 覆蓋）
OUTPUT_CONSTRAINTS = """
【輸出約束——必須遵守】
1. 你只做「風控覆核」，不得更改既有的訊號分級與進場決策。
2. 只針對以下面向補充觀察：
   a. 這些量化數據之間是否有矛盾或異常
   b. 數據未涵蓋的常見風險（產業循環、財報時程、個股事件）
   c. 執行面注意事項（流動性、波動度）
3. 以繁體中文輸出，最多 80 字，不要列點、不要客套。
4. 若無特別觀察，回答：「數據一致，無額外風險」。"""


def resolve_ai_config(cfg_ai: dict) -> dict:
    """解析 AI 設定：環境變數 > config

    環境變數：OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
    """
    base_url = os.environ.get("OPENAI_BASE_URL") or cfg_ai.get("base_url") or ""
    api_key = os.environ.get("OPENAI_API_KEY") or cfg_ai.get("api_key") or ""
    model = os.environ.get("OPENAI_MODEL") or cfg_ai.get("model") or ""
    # 自動生效：base_url 與 model 齊備即啟用；
    # 僅在 config 明確設 "enabled": false 時才強制關閉（api_key 選配，本地端點可免）
    enabled = cfg_ai.get("enabled", True) and bool(base_url) and bool(model)
    return {
        "enabled": bool(enabled),
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "temperature": float(cfg_ai.get("temperature", 0.2)),
        "max_tokens": int(cfg_ai.get("max_tokens", 300)),
        "system_prompt": cfg_ai.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
    }


def build_context(row: dict) -> dict:
    """從 Top5 列組出餵給 AI 的精簡上下文"""
    keys = ["ticker", "name", "sector", "grade", "total",
            "f1", "f2", "f3", "f4", "f5",
            "eps_2026", "eps_2027", "growth_2026", "growth_2027",
            "rev_1m", "rev_3m", "analysts",
            "foreign_20d", "trust_20d", "main_force_20d",
            "dist_60d_high", "rsi14", "close", "ma20", "ma60",
            "entry_low", "entry_high", "stop_loss", "target_price", "rr"]
    return {k: row.get(k) for k in keys if row.get(k) is not None}


def _call_llm(messages: list[dict], ai_cfg: dict,
              session: Optional[requests.Session] = None) -> str:
    url = f"{ai_cfg['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if ai_cfg["api_key"]:
        headers["Authorization"] = f"Bearer {ai_cfg['api_key']}"
    payload = {
        "model": ai_cfg["model"],
        "messages": messages,
        "temperature": ai_cfg["temperature"],
        "max_tokens": ai_cfg["max_tokens"],
    }
    sess = session or requests
    r = sess.post(url, json=payload, headers=headers, timeout=90)
    if r.status_code != 200:
        hint = ""
        body = r.text[:150]
        if r.status_code in (401, 404) and "model" in body.lower():
            hint = (f"；請確認 model id 是否為該端點 /models 清單中的名稱"
                    f"（可查 {ai_cfg['base_url']}/models）")
        raise RuntimeError(f"HTTP {r.status_code}: {body}{hint}")
    return r.json()["choices"][0]["message"]["content"].strip()


def ai_evaluate(row: dict, cfg_ai: dict, cache=None,
                session: Optional[requests.Session] = None) -> str:
    """對單檔 Top5 標的取得 AI 質性評估；任何失敗回傳「—」說明"""
    ai_cfg = resolve_ai_config(cfg_ai)
    if not ai_cfg["enabled"]:
        return "—（AI 評估未啟用或未設定端點）"

    context = build_context(row)
    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    digest = hashlib.md5(
        (context_json + ai_cfg["model"]).encode()).hexdigest()[:12]

    def compute() -> str:
        messages = [
            {"role": "system",
             "content": f"{ai_cfg['system_prompt']}\n{OUTPUT_CONSTRAINTS}"},
            {"role": "user",
             "content": ("以下是台股波段策略量化篩選出的標的數據"
                         "（JSON）。請依系統指令進行風控覆核：\n"
                         + context_json)},
        ]
        try:
            text = _call_llm(messages, ai_cfg, session=session)
            # 防呆：去除 markdown 圍籬與過長輸出
            text = text.replace("```", "").strip()
            return text[:300]
        except Exception as e:  # noqa: BLE001
            logger.warning("AI 評估失敗（%s）：%s",
                           context.get("ticker"), str(e)[:100])
            return f"—（AI 未回應：{str(e)[:60]}）"

    key = f"pipeline_ai_{context.get('ticker', 'x')}_{digest}"
    if cache is not None:
        # 有內容才算完整可快取；「—」開頭（失敗）下次重試
        holder: dict = {}

        def _fetch():
            r = compute()
            holder["r"] = r
            return None if r.startswith("—") else r

        got = cache.get(key, _fetch, ttl=7 * 86400, skip_none=True)
        return got if isinstance(got, str) else holder.get("r", "—")

    return compute()
