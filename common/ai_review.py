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
import time
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


def _is_degenerate(text: str) -> bool:
    """偵測重複退化：同一行重複 ≥3 次，或開頭片段反覆出現"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 3 and len(set(lines)) <= len(lines) / 3:
        return True
    probe = text[:24]
    if len(text) > 72 and text.count(probe) >= 3:
        return True
    return False


def _call_llm(messages: list[dict], ai_cfg: dict,
              session: Optional[requests.Session] = None) -> str:
    """呼叫 chat/completions；429/5xx/連線錯誤自動重試（最多 3 次）

    參考 tw-quant-pickup 的可用實作：payload 僅送 model/messages/max_tokens，
    不送 temperature——部分免費模型上游對不相容參數會直接回 500/503。
    """
    base_url = ai_cfg["base_url"]
    # 部分 proxy 的 OPENAI_BASE_URL 已含完整路徑，避免雙重後綴
    if base_url.endswith("/chat/completions"):
        url = base_url
    else:
        url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if ai_cfg["api_key"]:
        headers["Authorization"] = f"Bearer {ai_cfg['api_key']}"
    payload: dict = {"model": ai_cfg["model"], "messages": messages}
    if ai_cfg.get("max_tokens"):
        payload["max_tokens"] = ai_cfg["max_tokens"]


    sess = session or requests
    attempts = int(ai_cfg.get("retries", 3))
    RETRYABLE = {429, 500, 502, 503, 504}
    last_err = "未知錯誤"

    for attempt in range(attempts):
        try:
            r = sess.post(url, json=payload, headers=headers, timeout=90)
        except requests.exceptions.RequestException as e:
            logger.warning("AI 呼叫連線失敗（attempt %d/%d）：%s",
                           attempt + 1, attempts, str(e)[:80])
            last_err = str(e)[:120]
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
            continue

        body = r.text[:150]
        if r.status_code == 200:
            try:
                msg = r.json()["choices"][0]["message"]
                text = str(msg.get("content") or "").strip()
                if not text:
                    # 推理模型常把輸出放 reasoning 欄；有就拿來用
                    reason = (msg.get("reasoning_content")
                              or msg.get("reasoning") or "")
                    if str(reason).strip():
                        text = str(reason).strip()[-300:]   # 取結尾結論
                        logger.info("AI content 為空，改用 reasoning 輸出")
            except (ValueError, KeyError, IndexError, TypeError):
                text = ""
            if not text:
                keys = []
                try:
                    msg = r.json()["choices"][0]["message"]
                    keys = [k for k in msg.keys()
                            if msg.get(k)]
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "AI 回應空內容（message 非空欄位：%s，attempt %d/%d）",
                    keys or "無", attempt + 1, attempts)
            if not text:
                # 部分推理模型在 max_tokens 內只輸出思考不輸出答案 → 空內容
                logger.warning("AI 回應空內容（attempt %d/%d），重試",
                               attempt + 1, attempts)
                last_err = "模型回傳空內容"
                if attempt < attempts - 1:
                    time.sleep(3)
                continue
            if _is_degenerate(text):
                logger.warning("AI 輸出重複退化（attempt %d/%d），重試",
                               attempt + 1, attempts)
                last_err = "輸出重複退化"
                if attempt < attempts - 1:
                    time.sleep(3)
                continue
            return text
        if r.status_code in RETRYABLE:
            logger.warning("AI 呼叫 HTTP %d（attempt %d/%d），稍後重試：%s",
                           r.status_code, attempt + 1, attempts, body)
            last_err = f"HTTP {r.status_code}: {body}"
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
            continue

        # 不可重試的客戶端錯誤（401/404 等）
        hint = ""
        if r.status_code in (401, 404) and "model" in body.lower():
            hint = (f"；請確認 model id 是否為該端點 /models 清單中的名稱"
                    f"（可查 {base_url}/models）")
        raise RuntimeError(f"HTTP {r.status_code}: {body}{hint}")

    raise RuntimeError(f"HTTP 錯誤（已重試 {attempts} 次）: {last_err}")


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
        # 備援模型鏈：主模型掛了依序嘗試 fallback_models
        models = [ai_cfg["model"]]
        models += [m for m in (ai_cfg.get("fallback_models") or [])
                   if m not in models]
        errs = []
        for m in models:
            if len(models) > 1:
                logger.info("AI 評估使用模型：%s", m)
            try:
                text = _call_llm(messages, dict(ai_cfg, model=m),
                                 session=session)
                text = text.replace("```", "").strip()
                return f"{text}（via {m}）"[:300] if len(models) > 1 else text
            except Exception as e:  # noqa: BLE001
                errs.append(f"{m}: {str(e)[:80]}")
                logger.warning("AI 評估失敗（%s @ %s）：%s",
                               context.get("ticker"), m, str(e)[:100])
        return f"—（AI 未回應；{'；'.join(errs)[:150]}）"

    key = f"pipeline_ai_{context.get('ticker', 'x')}_{digest}"

    def _valid(s) -> bool:
        """有效快取：非空、非失敗標記、非重複退化"""
        return (isinstance(s, str) and s.strip()
                and not s.startswith("—") and not _is_degenerate(s))

    if cache is not None:
        # 讀既有快取；無效（空/退化/失敗字串）→ 視同未命中重新計算並覆寫
        existing = cache.get(key, lambda: None,
                             ttl=7 * 86400, skip_none=True)
        if _valid(existing):
            return existing

    result = compute()

    if cache is not None and _valid(result):
        cache.save_disk_cache(
            {key: {"data": result, "_ts": __import__("time").time()}})

    return result
