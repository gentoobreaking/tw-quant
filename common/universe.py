"""Stage 0 股票池建構（T006）— ETF 排名取 Top5 與成分股合併去重

報酬定義：近三年「純價格報酬」（未還原 Close 首尾比，不含配息再投資）。
嚴禁使用 Adj Close / auto_adjust=True / TaiwanStockPriceAdj。
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from .logger import logger


def normalize_ticker(sym: str) -> Optional[str]:
    """Yahoo 代號正規化為 4 位數字普通股；無法正規化回 None

    例：'2330.TW'→'2330'、'3260O'（上櫃標記）→'3260'、'0050B.TW'→None
    """
    sym = sym.strip().replace(".TW", "").replace(".TWO", "")
    # Yahoo 上櫃標記：尾碼 O
    if len(sym) == 5 and sym.endswith("O") and sym[:4].isdigit():
        sym = sym[:-1]
    if not (len(sym) == 4 and sym.isdigit()):
        return None
    return sym


def rank_etfs(cfg: dict, download_fn: Optional[Callable] = None
              ) -> list[dict]:
    """近三年純價格報酬排名，取 cfg['top_n_etf'] 名

    回傳 [{"ticker","name","category","return_3y"}, ...]（降序）
    """
    import yfinance as yf

    candidates = cfg["etf_candidates"]
    tickers = [c["ticker"] for c in candidates]
    download = download_fn or _default_download

    close_df = download(tickers)
    rows = []
    for c in candidates:
        t = c["ticker"]
        if t not in close_df.columns:
            logger.warning("ETF %s 無價格資料，跳過", t)
            continue
        s = close_df[t].dropna()
        if s.empty:
            logger.warning("ETF %s 價格序列為空，跳過", t)
            continue
        years = (s.index[-1] - s.index[0]).days / 365.25
        ret = float(s.iloc[-1]) / float(s.iloc[0]) - 1.0
        note = "" if years >= 2.9 else f"（樣本僅 {years:.1f} 年）"
        rows.append({**c, "return_3y": ret, "_note": note})

    rows.sort(key=lambda r: r["return_3y"], reverse=True)
    top_n = int(cfg.get("top_n_etf", 5))
    top = rows[:top_n]
    for r in top:
        logger.info("ETF Top%d：%s %s 三年報酬 %.1f%% %s",
                    top.index(r) + 1, r["ticker"], r["name"],
                    r["return_3y"] * 100, r["_note"])
    return top


def _default_download(tickers: list[str]) -> pd.DataFrame:
    """未還原日線 Close——auto_adjust 必須為 False（不含配息再投資）"""
    import yfinance as yf
    df = yf.download(tickers, period="3y", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        return df["Close"]
    return df[["Close"]].rename(columns={"Close": tickers[0]})


def build_pool(top5_etfs: list[dict],
               holdings_fn: Callable[[str], Optional[list[str]]],
               min_pool_size: int = 50,
               deep_holdings_fn: Optional[Callable[[str, int], list[str]]] = None
               ) -> pd.DataFrame:
    """成分股合併去重，不足 min_pool_size 時延伸持股深度

    holdings_fn(etf_ticker) -> list[str]（前十大，權重順序）
    deep_holdings_fn(etf_ticker, depth) -> list[str]（前 depth 名，可選）

    回傳欄位：ticker,etf_sources,count（排序：count 降序 → Top1 權重順序）
    """
    exclude = {normalize_ticker(e["ticker"]) or e["ticker"]
               for e in top5_etfs}
    pool: dict[str, dict] = {}
    top1_order: dict[str, int] = {}

    def add_from(etf_ticker: str, symbols: list[str]):
        for rank, sym in enumerate(symbols):
            norm = normalize_ticker(sym)
            if norm is None or norm in exclude:
                continue
            entry = pool.setdefault(
                norm, {"ticker": norm, "etf_sources": [], "count": 0,
                       "_first_rank": len(pool)})
            if etf_ticker not in entry["etf_sources"]:
                entry["etf_sources"].append(etf_ticker)
                entry["count"] += 1
            if etf_ticker == top5_etfs[0]["ticker"] and norm not in top1_order:
                top1_order[norm] = rank

    # Pass 1：各 ETF 前十大
    for etf in top5_etfs:
        holdings = holdings_fn(etf["ticker"]) or []
        add_from(etf["ticker"], holdings)

    # Pass 2：不足額時延伸持股至第 15、20 名
    warned_deep = False
    for depth in (15, 20):
        if len(pool) >= min_pool_size:
            break
        if deep_holdings_fn is None:
            if not warned_deep:
                logger.warning("免費來源僅前十大持股，無法補足至 %d 檔"
                               "（目前 %d 檔），續行", min_pool_size, len(pool))
                warned_deep = True
            break
        extended = False
        for etf in top5_etfs:
            extra = deep_holdings_fn(etf["ticker"], depth)
            before = len(pool)
            add_from(etf["ticker"], extra)
            if len(pool) > before:
                extended = True
            if len(pool) >= min_pool_size:
                break
        if not extended:
            logger.warning("延伸持股至第 %d 名已無新增標的，停止補足（%d 檔）",
                           depth, len(pool))
            break

    rows = []
    for entry in pool.values():
        rows.append({
            "ticker": entry["ticker"],
            "etf_sources": "|".join(entry["etf_sources"]),
            "count": entry["count"],
            "_top1_rank": top1_order.get(entry["ticker"], 999),
            "_first_rank": entry["_first_rank"],
        })
    df = pd.DataFrame(rows, columns=["ticker", "etf_sources", "count",
                                     "_top1_rank", "_first_rank"])
    df = df.sort_values(["count", "_top1_rank", "_first_rank"],
                        ascending=[False, True, True]).reset_index(drop=True)
    logger.info("股票池建構完成：%d 檔（目標 ≥%d）", len(df), min_pool_size)
    return df
