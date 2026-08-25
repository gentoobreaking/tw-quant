#!/usr/bin/env python3
"""LLM 模型可用性測試 — 掃描端點上哪些模型真的能回應

用法：
    # 只列端點上的模型（不發對話請求）
    python3 scripts/check_llm_models.py --list

    # 測試全部模型的可用性（每個模型送一個 ping，耗少量 token）
    python3 scripts/check_llm_models.py --all

    # 只測指定的幾個
    python3 scripts/check_llm_models.py --models "x-preview-f-free,laguna-s-2.1-free"

環境變數：OPENAI_BASE_URL（必填）、OPENAI_API_KEY（多數閘道必填）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests


def _display_width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _display_width(s))


def render_box(headers: list[str], rows: list[list[str]]) -> str:
    widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(str(cell)))

    def line(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def fmt(cells):
        cells = [str(c) for c in cells] + [""] * (len(headers) - len(cells))
        return ("│ " + " │ ".join(
            _pad(c, widths[i]) for i, c in enumerate(cells)) + " │")

    out = [line("┌", "┬", "┐"), fmt(headers), line("├", "┼", "┤")]
    for row in rows:
        out.append(fmt(row))
        out.append(line("├", "┼", "┤"))
    out[-1] = line("└", "┴", "┘")
    return "\n".join(out)


def list_models(base_url: str, api_key: str) -> list[str]:
    r = requests.get(f"{base_url}/models",
                     headers={"Authorization": f"Bearer {api_key}"},
                     timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"/models HTTP {r.status_code}: {r.text[:120]}")
    return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))


def ping_model(base_url: str, api_key: str, model: str,
               max_tokens: int, timeout: int) -> tuple[str, float]:
    """送最小對話請求，回傳（狀態, 延遲秒）"""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url}/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            json={"model": model,
                  "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": max_tokens},
            timeout=timeout)
    except requests.exceptions.Timeout:
        return f"❌ 逾時（>{timeout}s）", time.time() - t0
    except requests.exceptions.RequestException as e:
        return f"❌ {str(e)[:60]}", time.time() - t0

    elapsed = round(time.time() - t0, 2)
    if r.status_code == 200:
        try:
            content = (r.json()["choices"][0]["message"].get("content")
                       or "").strip()
            if content:
                return f"✅ 可用（{elapsed}s）", elapsed
            return "⚠️ 回應空內容（可能為推理模型，調大 max_tokens 再試）", elapsed
        except (ValueError, KeyError):
            return f"⚠️ HTTP 200 但回應格式異常", elapsed

    reason = r.text[:80].replace("\n", " ")
    if r.status_code == 401:
        return f"❌ 401 無權限", elapsed
    if r.status_code == 402:
        return f"❌ 402 額度用盡", elapsed
    if r.status_code == 403:
        return f"❌ 403 被拒", elapsed
    if r.status_code in (429,):
        return f"⚠️ 429 限流（稍後再試）", elapsed
    if r.status_code >= 500:
        return f"⚠️ 上游不可用（{r.status_code}）：{reason}", elapsed
    return f"❌ HTTP {r.status_code}: {reason}", elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 模型可用性測試")
    ap.add_argument("--list", action="store_true", help="只列出模型清單")
    ap.add_argument("--all", action="store_true",
                    help="測試端點上所有模型（會消耗少量 token）")
    ap.add_argument("--models", default="",
                    help="逗號分隔的指定模型清單（優先於 --all 與 --free）")
    ap.add_argument("--free", action="store_true",
                    help="只測名稱含 free 的模型")
    ap.add_argument("--max-tokens", type=int, default=32,
                    help="每次測試的 max_tokens（預設 32，控制消耗）")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="逐模型測試間隔秒數（預設 1）")
    ap.add_argument("--timeout", type=int, default=60,
                    help="單一請求逾時秒數（預設 60）")
    args = ap.parse_args()

    base_url = (os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    if not base_url:
        print("❌ 未設定 OPENAI_BASE_URL")
        return 1

    try:
        ids = list_models(base_url, api_key)
    except RuntimeError as e:
        print(f"❌ 取得模型清單失敗：{e}")
        return 1
    print(f"端點：{base_url}｜模型共 {len(ids)} 個\n")

    if args.list or not (args.all or args.models or args.free):
        for i in ids:
            print(" -", i)
        free_ids = [i for i in ids if "free" in i.lower()]
        print(f"\n免費模型 {len(free_ids)} 個：")
        for i in free_ids:
            print(" -", i)
        print("\n提示：--free 測免費模型／--all 全測／--models \"a,b\" 指定")
        return 0

    targets = [m.strip() for m in args.models.split(",") if m.strip()] \
        if args.models else ids
    if args.free and not args.models:
        targets = [m for m in targets if "free" in m.lower()]
    unknown = [m for m in targets if m not in ids]
    if unknown:
        print(f"⚠️ 以下模型不在端點清單中（仍嘗試）：{', '.join(unknown)}")

    rows = []
    ok_count = 0
    for m in targets:
        status, _ = ping_model(base_url, api_key, m,
                               args.max_tokens, args.timeout)
        if status.startswith("✅"):
            ok_count += 1
        rows.append([m, status])
        print(f"  測試中… {m}", file=sys.stderr)
        time.sleep(args.delay)

    print(render_box(["模型", "狀態"], rows))
    print(f"\n結果：{ok_count}/{len(targets)} 個模型可用"
          f"｜max_tokens={args.max_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
