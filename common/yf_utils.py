"""yfinance 批次下載、序列化輔助"""
import os
import time
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd
import yfinance as yf

from .cache import DiskCache
from .rate_limit import RateLimiter
from .serialization import df_to_dict, dict_to_df

_yf_cache_warmed = False


def _warm_yf_cache():
    """預初始化 yfinance 內部 peewee/SQLite 緩存，避免多線程同時 create_tables 導致鎖衝突"""
    global _yf_cache_warmed
    if _yf_cache_warmed:
        return
    _yf_cache_warmed = True
    try:
        yf.download("2330.TW", period="5d", progress=False, auto_adjust=True)
    except Exception:
        pass


def _download_single(ticker: str, days: int, rate_limiter: RateLimiter, retries: int = 3):
    """單檔下載，具重試機制 (threads=False 避免內部多線程)"""
    for attempt in range(retries):
        rate_limiter.wait("yf")
        try:
            df = yf.download(ticker, period=f"{days}d", progress=False, auto_adjust=True, threads=False)
            if df is not None and not df.empty and len(df) >= 120:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]
                df = df.dropna(subset=["close"])
                if len(df) >= 120:
                    return df
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)
    return None


def batch_prefetch_prices(tickers: List[str], cache: DiskCache,
                          rate_limiter: RateLimiter, retries: int = 3,
                          days: int = 400) -> None:
    """批次下載 yfinance 日線，存入 DiskCache"""
    _warm_yf_cache()

    uncached = []
    for ticker in tickers:
        ck = f"yf_{ticker}_{days}d"
        if cache.get(ck, lambda: None, skip_none=True) is None:
            uncached.append(ticker)
    if not uncached:
        return

    batch_size = 10
    for i in range(0, len(uncached), batch_size):
        batch = uncached[i:i + batch_size]
        for attempt in range(retries):
            rate_limiter.wait("yf")
            try:
                df = yf.download(" ".join(batch), period=f"{days}d",
                                 progress=False, auto_adjust=True)
                if df is None or df.empty:
                    raise RuntimeError("empty response")

                now_ts = time.time()
                if isinstance(df.columns, pd.MultiIndex):
                    success = False
                    for t in batch:
                        cols = [c for c in df.columns if c[1] == t]
                        if not cols:
                            continue
                        tdf = df[list(cols)]
                        tdf.columns = [c[0].lower() for c in tdf.columns]
                        tdf = tdf.dropna(subset=["close"])
                        if len(tdf) < 120:
                            continue
                        ck = f"yf_{t}_{days}d"
                        cache.get(ck, lambda _tdf=tdf: df_to_dict(_tdf))
                        success = True
                    if not success:
                        raise RuntimeError("no valid tickers in batch")
                else:
                    df.columns = [c.lower() for c in df.columns]
                    ck = f"yf_{batch[0]}_{days}d"
                    cache.get(ck, lambda: df_to_dict(df))
                break
            except Exception as e:
                if attempt < retries - 1:
                    wait = (attempt + 1) * 5
                    print(f"\n  [batch {i // batch_size + 1} 重試 {attempt + 1}/{retries} 等待 {wait}s: {e}]")
                    time.sleep(wait)
                else:
                    print(f"\n  [batch 下載失敗 {batch[0]}…({len(batch)}檔): {e}，改用單檔下載]")
                    _prefetch_fallback(batch, cache, rate_limiter, retries, days)


def _prefetch_fallback(tickers: List[str], cache: DiskCache,
                       rate_limiter: RateLimiter, retries: int, days: int) -> None:
    """批量失敗時回退為逐檔下載"""
    failed = 0
    for t in tickers:
        ck = f"yf_{t}_{days}d"
        df = _download_single(t, days, rate_limiter, retries=retries)
        if df is not None:
            try:
                cache.get(ck, lambda _df=df: df_to_dict(_df))
            except Exception:
                failed += 1
        else:
            failed += 1
    if failed:
        print(f"    {failed}/{len(tickers)} 檔單檔下載失敗")


def fetch_price(ticker_yf: str, cache: DiskCache, rate_limiter: RateLimiter,
                retries: int = 3, days: int = 400, ttl: Optional[int] = None
                ) -> Optional[pd.DataFrame]:
    """取得單檔日線 DataFrame（優先從快取）"""
    ck = f"yf_{ticker_yf}_{days}d"
    cached = cache.get(ck, lambda: None, ttl=ttl, skip_none=True)
    if cached is not None:
        df = dict_to_df(cached)
        if df is not None and len(df) >= 120:
            return df

    for attempt in range(retries):
        rate_limiter.wait("yf")
        try:
            df = yf.download(ticker_yf, period=f"{days}d", progress=False, auto_adjust=True)
            if df.empty or len(df) < 120:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close"])
            if len(df) < 120:
                return None
            cache.get(ck, lambda: df_to_dict(df), ttl=ttl)
            return df
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)
                continue
            return None
    return None


def fetch_info(ticker_yf: str, cache: DiskCache, rate_limiter: RateLimiter,
               retries: int = 3, ttl: Optional[int] = None) -> dict:
    """取得 yfinance Ticker.info"""
    ck = f"yf_info_{ticker_yf}"
    cached = cache.get(ck, lambda: None, ttl=ttl, skip_none=True)
    if cached is not None and isinstance(cached, dict):
        return cached

    for attempt in range(retries):
        rate_limiter.wait("yf")
        try:
            t = yf.Ticker(ticker_yf)
            info = t.info
            if info and isinstance(info, dict):
                cache.get(ck, lambda: info, ttl=ttl)
                return info
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)
                continue
    return {}


def get_stock_info(ticker_yf: str, cache: DiskCache, rate_limiter: RateLimiter,
                   retries: int = 3) -> dict:
    """統一取得個股資訊，含多層備援與 .TWO (OTC) fallback

    優先序:
      1. yfinance Ticker.info (.TW)
      2. .TWO fallback (OTC 上櫃股)
      3. sharesOutstanding × 收盤價
      4. 資產負債表 普通股股本 ÷ 10 × 收盤價
    """
    info = fetch_info(ticker_yf, cache, rate_limiter, retries=retries)
    fallback_two = None

    if not info.get("marketCap") and ticker_yf.endswith(".TW"):
        otc = ticker_yf.replace(".TW", ".TWO")
        fallback_info = fetch_info(otc, cache, rate_limiter, retries=retries)
        if fallback_info.get("marketCap"):
            return fallback_info
        fallback_two = fallback_info

    # 備援 1: sharesOutstanding × price
    shares = info.get("sharesOutstanding") or (fallback_two or {}).get("sharesOutstanding")
    target = ticker_yf if info else (ticker_yf.replace(".TW", ".TWO") if ticker_yf.endswith(".TW") else ticker_yf)
    if shares:
        price_df = fetch_price(target, cache, rate_limiter, retries=retries, days=5)
        if price_df is not None and not price_df.empty:
            price = float(price_df["close"].iloc[-1])
            merged = {**info, **({"marketCap": shares * price} or {})}
            _cache_info(ticker_yf, cache, merged)
            return merged

    # 備援 2: 資產負債表 普通股股本 ÷ 面額(台股10元) × 價格
    for bs_ticker in (ticker_yf, ticker_yf.replace(".TW", ".TWO") if ticker_yf.endswith(".TW") else None):
        if bs_ticker is None:
            continue
        bs = fetch_financials(bs_ticker, cache, rate_limiter, retries=retries).get("bs")
        if bs is None or bs.empty:
            continue
        for label in ("普通股股本", "普通股股數", "commonStock",
                      "Ordinary Shares Number", "Total Capital Stock"):
            try:
                val = bs.loc[label]
                if hasattr(val, 'iloc'):
                    val = val.iloc[0]
                shares = float(val)
                if shares <= 0:
                    continue
                if label in ("普通股股本", "commonStock", "Total Capital Stock"):
                    shares /= 10.0
                price_df = fetch_price(bs_ticker, cache, rate_limiter, retries=retries, days=5)
                if price_df is not None and not price_df.empty:
                    price = float(price_df["close"].iloc[-1])
                    merged = {**info, **({"marketCap": shares * price} or {})}
                    _cache_info(ticker_yf, cache, merged)
                    return merged
            except (KeyError, IndexError, TypeError, ValueError):
                continue

    return info


def _cache_info(ticker_yf: str, cache: DiskCache, info: dict):
    ck = f"yf_info_{ticker_yf}"
    cache.get(ck, lambda: info)


def fetch_financials(ticker_yf: str, cache: DiskCache, rate_limiter: RateLimiter,
                     retries: int = 3, ttl: Optional[int] = 604800) -> Dict[str, Optional[pd.DataFrame]]:
    """取得 yfinance 財報數據 (含快取, P2-1)
    
    回傳: {"bs": DataFrame, "fin": DataFrame}
    預設快取 7 天 (604800s)
    """
    ck_bs = f"yf_bs_{ticker_yf}"
    ck_fin = f"yf_fin_{ticker_yf}"
    
    res = {"bs": None, "fin": None}
    
    # 嘗試從快取讀取
    cached_bs = cache.get(ck_bs, lambda: None, ttl=ttl, skip_none=True)
    cached_fin = cache.get(ck_fin, lambda: None, ttl=ttl, skip_none=True)
    
    if cached_bs is not None:
        res["bs"] = dict_to_df(cached_bs)
    if cached_fin is not None:
        res["fin"] = dict_to_df(cached_fin)

    if res["bs"] is not None and res["fin"] is not None:
        return res

    # 未命中或過期，從 yf 抓取
    for attempt in range(retries):
        rate_limiter.wait("yf")
        try:
            t = yf.Ticker(ticker_yf)
            bs = t.quarterly_balance_sheet
            fin = t.quarterly_financials

            if bs is not None and not bs.empty:
                cache.get(ck_bs, lambda: df_to_dict(bs), ttl=ttl)
                res["bs"] = bs
            if fin is not None and not fin.empty:
                cache.get(ck_fin, lambda: df_to_dict(fin), ttl=ttl)
                res["fin"] = fin

            if res["bs"] is not None or res["fin"] is not None:
                break
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)
                continue

    return res


def get_exchange(ticker: str) -> str:
    """回傳 'TW' (上市) 或 'TWO' (上櫃)，使用 twstock.codes 判斷"""
    try:
        import twstock
        if ticker in twstock.codes:
            mkt = twstock.codes[ticker].market
            return "TWO" if mkt == "上櫃" else "TW"
    except Exception:
        pass
    return "TW"
