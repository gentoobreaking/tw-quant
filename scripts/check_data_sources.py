#!/usr/bin/env python3
"""資料源連線診斷 — 檢查找買點管線的所有外部依賴

用法：
    python3 scripts/check_data_sources.py

輸出：對齊的結果結果總表（✅/⚠️/❌）＋非可用項的詳細說明。
退出碼：全部核心來源可用=0，任一核心失敗=1。
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
# 核心來源失敗會影響管線主要功能
CORE = {"twse_openapi", "twse_t86", "yfinance", "finmind"}


def _display_width(s: str) -> int:
    """中日韓字元算 2 格寬"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _display_width(s))


# ---------------------------------------------------------------- 檢查函式
def check_twse_openapi() -> tuple[bool, str, str]:
    """月營收 YoY／季 EPS 主路徑"""
    r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                     headers=HEADERS, timeout=15)
    if r.status_code == 200 and isinstance(r.json(), list):
        return True, f"{len(r.json())} 筆月營收", ""
    return False, f"HTTP {r.status_code}", r.text[:100]


def check_twse_t86() -> tuple[bool, str, str]:
    """三大法人籌碼主路徑。今日盤後 16:00 才發布，自動往回找最近交易日"""
    d = dt.date.today()
    tried = []
    for _ in range(7):
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            j = requests.get(
                f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ds}"
                "&selectType=ALL&response=json",
                headers=HEADERS, timeout=15).json()
            stat = j.get("stat", "")
            if stat == "OK":
                return True, f"{d} 發布 {len(j.get('data', []))} 檔", ""
            tried.append(f"{ds}")
        d -= dt.timedelta(days=1)
    detail = "；".join(tried[:3])
    return False, "七日內無資料", \
        f"{detail}…；16:00 前查當日無資料屬正常"


def check_yfinance() -> tuple[bool, str, str]:
    """預估 EPS／日線主路徑"""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    info = yf.Ticker("2330.TW").get_info()
    eps = info.get("forwardEps")
    if info.get("symbol") or info.get("shortName"):
        return True, f"2330 forwardEps={eps}", ""
    return False, "有回應但無 symbol 資料", ""


def check_finmind() -> tuple[bool, str, str]:
    """備援：法人／營收／財報／日線。token 未填時為 guest 額度"""
    from common.cache import DiskCache
    from common.rate_limit import RateLimiter
    from common.finmind import FinMindClient
    import json as _json

    token = ""
    cfg = Path(__file__).resolve().parent.parent / "config_pipeline.json"
    if cfg.exists():
        token = _json.loads(cfg.read_text(encoding="utf-8")).get(
            "finmind_token", "") or ""

    rl = RateLimiter({"finmind": {"delay": 0.7, "jitter": 0.2}})
    fm = FinMindClient(token=token or None, rate_limiter=rl)
    rows = fm.fetch_dataset("TaiwanStockPrice", "2330",
                            start_date="2026-08-20", end_date="2026-08-25")
    fm.close()
    mode = "token" if token else "guest"
    if rows:
        return True, f"{mode} 模式，{len(rows)} 筆日線", ""
    hint = ("402=額度用盡（免費 600/hr）" if mode == "token"
            else "guest 額度低，建議填 config_pipeline.json finmind_token")
    return False, f"無資料（{mode}）", hint


def check_moneydj() -> tuple[bool, str, str]:
    """族群分類（走 TWCA TLS 備援）"""
    from common.moneydj import fetch_industry_index
    from common.tls_fallback import make_twca_session
    index = fetch_industry_index(make_twca_session())
    if len(index) >= 1000:
        return True, f"{len(index)} 個產業分類", ""
    if index:
        return False, f"僅 {len(index)} 分類（預期 ≥1000）", ""
    return False, "索引抓取失敗", ""


def check_tdcc() -> tuple[bool, str, str]:
    """大戶持股比率（集保；同樣踩 TWCA 缺 SKI，已內建備援）"""
    from common.tdcc import TDCCQuery
    from common.rate_limit import RateLimiter
    tdcc = TDCCQuery(RateLimiter({"tdcc": {"delay": 3.0, "jitter": 0.5}}),
                     retries=1)
    dates = tdcc.available_dates()
    if dates:
        return True, f"最新集保日 {dates[0]}", ""
    return False, "available_dates 失敗", "SSL 備援亦未通，見 logs"


CHECKS = [
    ("twse_openapi", "TWSE OpenAPI", check_twse_openapi,
     "營收/EPS 主路徑"),
    ("twse_t86", "TWSE fund/T86", check_twse_t86,
     "籌碼主路徑"),
    ("yfinance", "yfinance", check_yfinance,
     "EPS 預估——唯一免費源"),
    ("finmind", "FinMind", check_finmind,
     "全因子備援通道"),
    ("moneydj", "MoneyDJ", check_moneydj,
     "族群分類"),
    ("tdcc", "TDCC 集保", check_tdcc,
     "大戶持股"),
]

SYMBOL = {True: "✅ 可用", False: "❌ 失敗"}


class _CollectHandler(logging.Handler):
    """把 log 紀錄收進 list，最後統一輸出到報表底部"""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def main() -> int:
    # 過程中的 log 先收集起來，放到輸出最下方（不干擾結果表格）
    collected = _CollectHandler()
    collected.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    saved_handlers = []
    for name in ("tw_quant", "pipeline"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            saved_handlers.append((lg, h))
            lg.removeHandler(h)
        lg.addHandler(collected)

    results = []
    for key, name, fn, purpose in CHECKS:
        try:
            ok, summary, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, summary, detail = False, f"{type(e).__name__}", str(e)[:80]
        results.append((key, name, ok, summary, detail, purpose))

    # ---- 對齊總表 ----
    w_name = max(_display_width(r[1]) for r in results) + 2
    w_status = _display_width("✅ 可用") + 2
    print("找買點管線 — 資料源診斷")
    print("-" * 62)
    print(f"{_pad('資料源', w_name)}{_pad('狀態', w_status)}用途")
    print("-" * 62)
    for _, name, ok, summary, _, purpose in results:
        print(f"{_pad(name, w_name)}{_pad(SYMBOL[ok], w_status)}{purpose}")
    print("-" * 62)

    # ---- 結論 ----
    failed_core = [k for k, _, ok, *_ in results if not ok and k in CORE]
    print()
    if failed_core:
        names = {k: n for k, n, *_ in CHECKS}
        print(f"❌ 核心來源失敗：{', '.join(names[k] for k in failed_core)}")
    else:
        print("✅ 全部核心來源可用")

    # ---- 詳細資訊 ----
    notes = [(name, summary, detail, ok) for _, name, ok, summary, detail, _
             in results if not ok or detail]
    if notes:
        print("\n詳細資訊：")
        for name, summary, detail, ok in notes:
            mark = "✅" if ok else "⚠️ "
            line = f"- {name}：{summary}"
            if detail:
                line += f"（{detail}）"
            print(f"{mark}{line}")

    # ---- 執行紀錄（log）放最下方 ----
    for lg, h in saved_handlers:
        lg.addHandler(h)
    if collected.lines:
        print("\n" + "-" * 62)
        print("執行紀錄（log）：")
        for line in collected.lines:
            print(line)
    return 0 if not failed_core else 1


if __name__ == "__main__":
    sys.exit(main())
