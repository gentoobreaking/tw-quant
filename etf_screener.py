"""
ETF 篩選腳本 v4 — Dual-track 市值型/高股息 + 特殊 槓桿/反向
============================================================================
資料源: yfinance + TWSE fund/T86, TWT93U + TDCC 集保
共用模組: common/ (cache, rate_limit, twse, tdcc, yf_utils, kd, serialization)

條件總表:
  C1    收盤 > 60MA 且 60MA 向上
  C2    收盤 > 20MA
  C3    前 N 日最低曾跌破 20MA
  C4    量 > 5日均量 AND 量 > 20日均量
  C5    溢價 < 3% (非TWD跳過, 折價加分)
  C6    成分股 >= 2/3 在季線上 (部分ETF)
  C7    類型特定 (費用率/⚠高股息需手動)
  C8    ⚠高股息需手動查填息
  C11   法人大戶申購/買超
  C12   大戶收 + 規模趨勢
  C13   非散戶接陷阱 + 非規模縮水
  C14   60MA回測買點 (type-adjusted)
  C15   連3日上漲確認 (type-adjusted)
  C16   未翻倍排除 (type-adjusted)
  C17   非高檔震排除
  C18   攻擊訊號 (60分金叉 OR 帶量紅K)
  C19   多頭買點 (>60MA + KD金叉)
  C20   空頭賣點排除
  操作紀律 (type-adjusted)
============================================================================
"""

import time
import warnings
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
import os
import random
from tqdm import tqdm
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

warnings.filterwarnings("ignore", message=".*urllib3.*OpenSSL.*")
warnings.filterwarnings("ignore", category=UserWarning)

from common import (
    load_config, DiskCache, RateLimiter, logger,
    twse_json, twse_data,
    TDCCQuery, batch_prefetch_prices,
    fetch_price as _common_fetch_price, fetch_info as _common_fetch_info,
    get_exchange, fetch_top10_holdings,
    calc_kd, df_to_dict, dict_to_df,
    calc_score, check_hard_reject, classify_tier, check_exit,
    ScreeningResult, save_results,
    ETF_SCORE_WEIGHTS, TIER_ENTER, TIER_WATCH, TIER_EXIT, TIER_OUT,
)

# ---- 設定檔 ----
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_etf.json")

_DEFAULTS = {
    "cache_ttl_seconds": 7200,
    "request_retries": 3,
    "rate_limit": {
        "twse": {"delay": 2.0, "jitter": 0.5},
        "tdcc": {"delay": 3.0, "jitter": 0.5},
        "yf":   {"delay": 1.0, "jitter": 0.3}
    },
    "scoring_weights": {
        "c1": 10, "c2": 5, "c3": 5, "c4": 10,
        "c5": 5, "c6": 5, "c7": 5, "c8": 5,
        "c11": 10, "c12": 10, "c13": 5,
        "c14": 5, "c15": 5, "c18": 10,
        "c19_bonus": 5
    },
    "hard_reject_rules": {
        "c16": False,
        "c17": False,
        "c20": True,
        "c13": False
    },
    "conditions": {
        "C1":  {"ma_period": 60, "trend_check_days": 5},
        "C2":  {"ma_period": 20},
        "C3":  {"ma_period": 20, "lookback_days": 5},
        "C4":  {"volume_ma_period": 5, "volume_ma_long_period": 20, "volume_ratio_min": 1.0},
        "C5":  {"premium_max": 3.0, "discount_bonus": True},
        "C6":  {"ma_period": 60, "min_stocks_above": 2},
        "C7":  {"expense_ratio_max": 0.43},
        "C8":  {"fill_days_max": 30, "divs_to_check": 3, "min_fill_ratio": 0.6},
        "C11": {"ma_period": 20, "lookback_window": 20, "consecutive_days": 3},
        "C12": {"large_share_threshold": 1000000, "weeks_to_check": 3, "trend_min": -0.5},
        "C13": {"lookback_days": 3},
        "C14": {"ma_period": 60, "proximity_min": 0.0, "proximity_max": 5.0,
                 "type_adjusted": {"高股息": {"proximity_max": 8.0}, "槓桿/反向型": {"proximity_max": 10.0}}},
        "C15": {"consecutive_up_days": 3,
                 "type_adjusted": {"槓桿/反向型": {"relaxed_mode": True, "up_days_in_window": 3, "window_days": 5}}},
        "C16": {"lookback_days": 40, "gain_max": 80.0,
                 "type_adjusted": {"高股息": {"gain_max": 40.0}, "槓桿/反向型": {"gain_max": 80.0, "lookback_days": 20}}},
        "C17": {"near_high_ratio": 0.85, "ma_period": 20},
        "C18": {"fast_ma_period_60min": 20, "slow_ma_period_60min": 60,
                "volume_ma_period": 5, "volume_ratio_min": 1.2, "yf_60min_period": "1mo"},
        "C19": {"ma_period": 60, "kd_period": 20, "k_smooth": 5, "d_smooth": 5,
                "kd_threshold": 20, "kd_check_offset": 1},
        "C20": {"ma_period": 60, "kd_period": 20, "k_smooth": 5, "d_smooth": 5,
                "kd_threshold": 80, "kd_check_offset": 1}
    },
    "alerts": {
        "break_ma": {"ma_period": 20, "consecutive_above_days": 2},
        "break_k_low": {"lookback_window": 20, "body_ratio_min": 0.5, "volume_ratio_min": 1.3}
    },
    "tier_thresholds": {
        "enter_min": 75,
        "watch_min": 40
    },
    "exit_params": {
        "ma20_period": 20, "ma60_period": 60,
        "lookback_days_e1": 30, "lookback_days_e2": 40,
        "high_ratio_e2": 0.95, "volume_ratio_e4": 0.7
    },
    "batch": {"inter_stock_delay": 0.3}
}

CONFIG = load_config(_CONFIG_PATH, _DEFAULTS)
_REQ_RETRIES = CONFIG["request_retries"]

# ---- 共用基礎設施 ----
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_etf")
_CACHE_DB = os.path.join(_CACHE_DIR, "tw_quant_etf.db")
_CACHE_TTL = CONFIG["cache_ttl_seconds"]

cache = DiskCache(_CACHE_DB, ttl=_CACHE_TTL)
rate_limiter = RateLimiter(CONFIG["rate_limit"])
tdcc = TDCCQuery(rate_limiter, retries=_REQ_RETRIES,
                 large_share_threshold=CONFIG["conditions"]["C12"]["large_share_threshold"])

_RESOLVED_ETF_TICKERS: dict[str, str] = {}


# ---- ETF 分類 ----
def classify_etf(ticker: str, info: dict) -> str:
    ticker_upper = ticker.upper()
    if ticker_upper.endswith("L") or ticker_upper.endswith("R"):
        return "槓桿/反向型"
    
    name = info.get("shortName", "") or info.get("longName", "")
    
    # P3-5: 穩定化 dividendYield 判斷 (高股息門檻 4%)
    dy = info.get("dividendYield")
    if dy is not None:
        val = dy if dy < 1.0 else dy / 100.0
        if val > 0.15:
            val = 0.0   # >15% 殖利率幾乎可確定是壞資料
        if val >= 0.04:
            return "高股息"

    if any(kw in name for kw in ["高股息", "息", "配息", "收益"]):
        return "高股息"
    # 若名稱含 ETN 則標記
    if "ETN" in ticker_upper or "etn" in name.lower():
        return "ETN"
    return "市值型"


# ---- 價格取得 (包裝 common) ----
def _fetch_price(ticker_yf: str, days: int = 400):
    return _common_fetch_price(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES, days=days)

def _fetch_info(ticker_yf: str) -> dict:
    return _common_fetch_info(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES)


# ---- TWSE API (包裝 common) ----
_T86_CACHE = {}
_T93_CACHE = {}
_TWSE_LOCK = threading.Lock()
_ASSET_LOCK = threading.Lock()


def _t86_by_date(ds: str):
    with _TWSE_LOCK:
        if ds not in _T86_CACHE:
            _T86_CACHE[ds] = cache.get(
                f"t86_{ds}",
                lambda: {row[0]: row for row in twse_data("fund/T86", ds, "", rate_limiter=rate_limiter)
                         if row and len(row) > 10}
            )
    return _T86_CACHE[ds]


def _t93_by_date(ds: str):
    with _TWSE_LOCK:
        if ds not in _T93_CACHE:
            _T93_CACHE[ds] = cache.get(
                f"t93_{ds}",
                lambda: {row[0]: row for row in twse_data("exchangeReport/TWT93U", ds, "", rate_limiter=rate_limiter)
                         if row and len(row) > 6}
            )
    return _T93_CACHE[ds]


# ============================================================
#  技術面 (C1~C4)
# ============================================================

def _safe_np(s):
    """Convert Series to float64 numpy array (None → NaN)"""
    return np.asarray(s, dtype="float64")


def check_tech(df) -> dict:
    c1_cfg = CONFIG["conditions"]["C1"]
    c2_cfg = CONFIG["conditions"]["C2"]
    c3_cfg = CONFIG["conditions"]["C3"]
    c4_cfg = CONFIG["conditions"]["C4"]

    c = _safe_np(df["close"])
    l = _safe_np(df["low"])
    v = _safe_np(df["volume"])
    ma20 = pd.Series(c).rolling(c2_cfg["ma_period"]).mean().to_numpy()
    ma60 = pd.Series(c).rolling(c1_cfg["ma_period"]).mean().to_numpy()
    v_ma5 = pd.Series(v).rolling(c4_cfg["volume_ma_period"]).mean().to_numpy()
    v_ma20 = pd.Series(v).rolling(c4_cfg["volume_ma_long_period"]).mean().to_numpy()
    i = -1

    trend_ok = not np.isnan(ma60[i - c1_cfg["trend_check_days"]]) and \
               ma60[i] > ma60[i - c1_cfg["trend_check_days"]]
    c1 = c[i] > ma60[i] and trend_ok
    c2 = c[i] > ma20[i]
    # C3: 回測 20MA（近 N 日低價曾觸及月線）
    # 與 C14 的差異：C3 是「低價碰月線」= 回測站回，C14 是「收盤距季線 X%」= 位階判斷
    c3l = c3_cfg["lookback_days"]
    c3 = any(
        (l[i - d] < ma20[i - d] if not np.isnan(ma20[i - d]) else False)
        for d in range(1, c3l + 1)
    )
    c4 = v[i] > v_ma5[i] and v[i] > v_ma20[i]

    return {
        "ok": c1 and c2 and c3 and c4,
        "c1": c1, "c2": c2, "c3": c3, "c4": c4,
        "close": round(c[i], 1), "ma20": round(ma20[i], 1), "ma60": round(ma60[i], 1),
        "vol_ratio": round(v[i] / v_ma5[i], 2) if v_ma5[i] > 0 else 0,
        "df": df,
        "detail": f"C1={'Y' if c1 else 'N'} C2={'Y' if c2 else 'N'} C3={'Y' if c3 else 'N'} C4={'Y' if c4 else 'N'}",
    }


# ============================================================
#  ETF 基本面 (C5~C8)
# ============================================================

def check_c5(df, info: dict) -> dict:
    """ETF 溢價/折價檢查"""
    c5_cfg = CONFIG["conditions"]["C5"]
    nav = info.get("navPrice")
    if nav is None or df is None:
        return {"ok": True, "detail": "C5=Y(無淨值資料)"}
    close = _safe_np(df["close"])[-1]
    if nav == 0:
        return {"ok": True, "detail": "C5=Y(淨值=0)"}

    # 幣別檢查
    currency = info.get("currency", "TWD")
    if currency.upper() != "TWD":
        return {"ok": True, "detail": f"C5=Y(非TWD幣別={currency}, 跳過)"}

    premium_pct = (close - nav) / nav * 100
    premium_max = c5_cfg["premium_max"]

    if premium_pct < 0:
        return {"ok": True, "detail": f"C5=Y(折價{premium_pct:.2f}% 👍)"}

    ok = premium_pct < premium_max
    sign = "+" if premium_pct >= 0 else ""
    return {"ok": ok, "detail": f"C5={'Y' if ok else 'N'}(溢價={sign}{premium_pct:.2f}%, 門檻{premium_max}%)"}


def check_c6(ticker: str, type_label: str) -> dict:
    if type_label == "槓桿/反向型" or type_label == "ETN":
        return {"ok": True, "detail": "C6=Y(不適用)"}
    c6_cfg = CONFIG["conditions"]["C6"]

    # P3-2: 優先從 yfinance 抓取成分股
    holdings = []
    try:
        info = _fetch_info(f"{ticker}.TW")
        yf_h = info.get("topHoldings")
        if isinstance(yf_h, list):
            # 取前 10 大
            holdings = [h.get("symbol", "").replace(".TW", "") for h in yf_h if h.get("symbol")][:10]
    except:
        pass

    if not holdings:
        holdings = fetch_top10_holdings(ticker, cache, rate_limiter, retries=_REQ_RETRIES) or []
        if not holdings:
            return {"ok": True, "detail": "C6=Y(無成分股資料)", "manual": True}
    holdings = [h for h in holdings if _is_tw_ticker(h)]
    non_empty = [h for h in holdings if h]
    if not non_empty:
        return {"ok": True, "detail": "C6=Y(無成分股資料)"}
    above = 0
    checked = 0
    for h in non_empty:
        exchange = get_exchange(h)
        hf = f"{h}.{exchange}"
        hdf = _fetch_price(hf, days=180)
        if hdf is None or len(hdf) < 60:
            continue
        hc = _safe_np(hdf["close"])
        hma60 = pd.Series(hc).rolling(c6_cfg["ma_period"]).mean().to_numpy()
        if not np.isnan(hma60[-1]) and hc[-1] > hma60[-1]:
            above += 1
        checked += 1
    if checked == 0:
        return {"ok": True, "detail": "C6=Y(無可查成分股)"}
    ok = above >= c6_cfg["min_stocks_above"]
    return {"ok": ok, "detail": f"C6={'Y' if ok else 'N'}(成分{above}/{checked}在季線上)"}


def check_c7(type_label: str, info: dict) -> dict:
    if type_label == "市值型":
        expense = info.get("netExpenseRatio")
        if expense is not None and isinstance(expense, (int, float)) and not np.isnan(expense):
            ok = expense < CONFIG["conditions"]["C7"]["expense_ratio_max"]
            return {"ok": ok, "detail": f"C7={'Y' if ok else 'N'}(費用率={expense:.2f}%)"}
        return {"ok": True, "detail": "C7=Y(無費用率資料)"}
    elif type_label == "高股息":
        return {"ok": True, "detail": "C7=⚠(高股息需手動查可分配收益)", "manual": True}
    else:
        return {"ok": True, "detail": "C7=Y(不適用)"}


def check_c8(type_label: str, ticker: str = "", df=None) -> dict:
    """C8 高股息填息檢查（自動化版）

    計算近 N 次除息後的填息狀態：
    - 近 3 次除息中至少 2 次在 30 個交易日內填息 → ok=True
    - 否則 → ok=False（填息失敗風險）

    使用 yfinance unadjusted close + dividend history 計算。
    """
    if type_label != "高股息":
        return {"ok": True, "detail": "C8=Y(不適用)"}

    c8_cfg = CONFIG["conditions"].get("C8", {})
    fill_days_max = c8_cfg.get("fill_days_max", 30)
    divs_to_check = c8_cfg.get("divs_to_check", 3)
    min_fill_ratio = c8_cfg.get("min_fill_ratio", 0.6)

    try:
        import yfinance as yf
        ticker_yf = _RESOLVED_ETF_TICKERS.get(ticker) or f"{ticker}.TW"

        # 快取 dividends 與價格資料
        cache_key_div = f"c8_divs_{ticker_yf}"
        cache_key_price = f"c8_price_{ticker_yf}"

        def _fetch_divs():
            yf_ticker = yf.Ticker(ticker_yf)
            divs = yf_ticker.dividends
            return divs.to_dict() if divs is not None and not divs.empty else None

        def _fetch_price():
            raw_df = yf.download(ticker_yf, period="1y", progress=False, auto_adjust=False)
            if raw_df is None or raw_df.empty:
                return None
            if isinstance(raw_df.columns, pd.MultiIndex):
                raw_df.columns = [c[0].lower() for c in raw_df.columns]
            return {"close": raw_df["close"].to_dict(), "index": [str(i) for i in raw_df.index]}

        divs_data = cache.get(cache_key_div, _fetch_divs, ttl=86400)
        price_data = cache.get(cache_key_price, _fetch_price, ttl=86400)

        if divs_data is None:
            return {"ok": True, "detail": "C8=⚠(無除息紀錄，需手動查)", "manual": True}

        divs = pd.Series(divs_data)
        divs.index = pd.to_datetime(divs.index)
        if divs.empty:
            return {"ok": True, "detail": "C8=⚠(無除息紀錄，需手動查)", "manual": True}

        if price_data is None:
            return {"ok": True, "detail": "C8=⚠(無價格資料，需手動查填息)", "manual": True}

        close = pd.Series(price_data["close"])
        close.index = pd.to_datetime(price_data["index"])

        fill_results = []

        for div_date, div_amount in recent_divs.items():
            if div_date.tz is not None:
                div_date = div_date.tz_localize(None)

            # 找除息日在 df 中的位置
            idx_arr = close.index.get_indexer([div_date], method='nearest')
            if len(idx_arr) == 0 or idx_arr[0] < 1:
                fill_results.append({"date": div_date.strftime("%m/%d"), "filled": None})
                continue

            idx = idx_arr[0]
            # 除息前一日收盤（unadjusted）
            pre_div_close = close.iloc[idx - 1]
            target_price = pre_div_close  # 填息目標 = 回到除息前收盤

            # 檢查後 fill_days_max 天內是否填息
            end_idx = min(idx + fill_days_max, len(close))
            filled = False
            fill_days = None
            for j in range(idx, end_idx):
                if close.iloc[j] >= target_price:
                    filled = True
                    fill_days = j - idx
                    break

            fill_results.append({
                "date": div_date.strftime("%m/%d"),
                "amount": round(float(div_amount), 2),
                "filled": filled,
                "fill_days": fill_days,
            })

        # 計算填息率
        valid_results = [r for r in fill_results if r["filled"] is not None]
        if not valid_results:
            return {"ok": True, "detail": "C8=⚠(無可計算除息資料)", "manual": True}

        fill_count = sum(1 for r in valid_results if r["filled"])
        fill_ratio = fill_count / len(valid_results)
        ok = fill_ratio >= min_fill_ratio

        # 組合明細
        details = []
        for r in valid_results:
            if r["filled"]:
                details.append(f"{r['date']}✅{r['fill_days']}d")
            else:
                details.append(f"{r['date']}❌未填")
        detail_str = f"C8={'Y' if ok else 'N'}(填息{fill_count}/{len(valid_results)}: {' '.join(details)})"

        return {"ok": ok, "detail": detail_str, "manual": False}

    except Exception as e:
        logger.warning("[%s] C8 填息檢查失敗: %s", ticker, e)
        return {"ok": True, "detail": "C8=⚠(填息檢查失敗，需手動查)", "manual": True}


# ============================================================
#  籌碼面 (C11~C13)
# ============================================================

def get_chip(stock_id: str, df):
    """三大法人買賣超 + 融資券（取近6個交易日）"""
    info = {"foreign_net": [], "trust_net": [], "dealer_net": [],
            "margin_balance": [],
            "dates": [], "margin_dates": []}
    if df is None or not isinstance(df.index, pd.DatetimeIndex):
        return info
    for dt in df.index[-6:]:
        ds = dt.strftime("%Y%m%d")
        t86 = _t86_by_date(ds)
        if stock_id in t86:
            def p(i): return int(t86[stock_id][i].replace(",", ""))
            info["foreign_net"].append(p(4))
            info["trust_net"].append(p(10))
            # 自營商買賣超(合計) — T86 [11]，安全取值
            try:
                info["dealer_net"].append(p(11) if len(t86[stock_id]) > 11 else 0)
            except (IndexError, ValueError):
                info["dealer_net"].append(0)
            info["dates"].append(ds)
        t93 = _t93_by_date(ds)
        if stock_id in t93:
            try:
                info["margin_balance"].append(int(t93[stock_id][6].replace(",", "")))
                info["margin_dates"].append(ds)
            except:
                info["margin_balance"].append(0)
                info["margin_dates"].append(ds)
    return info


def check_chip(info: dict, df, prev_ta=None, current_ta=None):
    c11_cfg = CONFIG["conditions"]["C11"]
    c13_cfg = CONFIG["conditions"]["C13"]

    c11 = c13 = False
    c11_detail = c13_detail = ""
    if df is None or not info.get("foreign_net"):
        return {"ok": False, "c11": False, "c13": False, "detail": "無籌碼資料"}

    c = _safe_np(df["close"])
    ma20 = pd.Series(c).rolling(c11_cfg["ma_period"]).mean().to_numpy()
    fn = info["foreign_net"]
    tn = info["trust_net"]
    mb = info["margin_balance"]
    chip_dates = info.get("dates", [])
    margin_dates = info.get("margin_dates", [])

    # C11: 找出近 N 日中收盤<MA20 的區間
    below_window = c11_cfg["lookback_window"]
    below_ma = [i for i in range(1, min(below_window + 1, len(c))) if c[-i] < ma20[-i]]
    if below_ma and chip_dates:
        below_dates_set = set()
        for i in below_ma:
            dt = df.index[-i]
            below_dates_set.add(dt.strftime("%Y%m%d"))

        fn_below = [fn[j] for j, d in enumerate(chip_dates) if d in below_dates_set]
        tn_below = [tn[j] for j, d in enumerate(chip_dates) if d in below_dates_set]

        if not fn_below and chip_dates:
            first_below = min(below_ma)
            last_below = max(below_ma)
            first_below_ds = df.index[-first_below].strftime("%Y%m%d")
            last_below_ds = df.index[-last_below].strftime("%Y%m%d")
            fn_below = [fn[j] for j, d in enumerate(chip_dates)
                        if last_below_ds <= d <= first_below_ds]
            tn_below = [tn[j] for j, d in enumerate(chip_dates)
                        if last_below_ds <= d <= first_below_ds]

        min_streak = c11_cfg["consecutive_days"]
        for arr, name in [(fn_below, "外資"), (tn_below, "投信")]:
            streak = 0
            for v in reversed(arr):
                if v > 0:
                    streak += 1
                    if streak >= min_streak:
                        c11 = True
                        c11_detail = f"{name}連買{streak}日"
                        break
                else:
                    streak = 0
            if c11:
                break
        if c11:
            c11_detail = f"法人大戶申購/買超({c11_detail})"
        else:
            c11_detail = "無連續買超"

    # C13: 法人大賣但融資大增（含自營商）+ 規模縮水
    dn = info.get("dealer_net", [])
    ld = c13_cfg["lookback_days"]
    if len(fn) >= ld and len(mb) >= ld and len(tn) >= ld:
        recent_fnet2 = sum(tn[-ld:]) + sum(fn[-ld:])
        # 加入自營商（若有資料）
        if len(dn) >= ld:
            recent_fnet2 += sum(dn[-ld:])

        if mb[-1] > mb[-ld] and recent_fnet2 < 0:
            c13 = True
            # 組合明細：哪些法人在賣
            sellers = []
            if sum(fn[-ld:]) < 0: sellers.append(f"外資{sum(fn[-ld:]):,}")
            if sum(tn[-ld:]) < 0: sellers.append(f"投信{sum(tn[-ld:]):,}")
            if len(dn) >= ld and sum(dn[-ld:]) < 0: sellers.append(f"自營商{sum(dn[-ld:]):,}")
            c13_detail = f"融資增{mb[-1]-mb[-ld]:,}, {'+'.join(sellers)}"
        else:
            # 補充：自營商大買 + 融資大增 → 疑似散戶透過自營商進場
            if len(dn) >= ld and len(mb) >= ld:
                dealer_buy = sum(dn[-ld:])
                margin_increase = mb[-1] - mb[-ld]
                if (dealer_buy > 0 and margin_increase > 0
                        and dealer_buy > margin_increase * 0.5):
                    c13 = True
                    c13_detail = f"自營商買{dealer_buy:,}+融資增{margin_increase:,}(疑似散戶)"

    if prev_ta is not None and current_ta is not None and df is not None:
        c = _safe_np(df["close"])
        price_rising = c[-1] > c[-5]
        asset_shrinking = current_ta < prev_ta
        if price_rising and asset_shrinking:
            shrink_pct = (prev_ta - current_ta) / prev_ta * 100
            c13 = True
            if c13_detail:
                c13_detail += f" | 規模縮水{shrink_pct:.1f}%"
            else:
                c13_detail = f"規模縮水{shrink_pct:.1f}%"

    detail_parts = []
    if c11_detail:
        detail_parts.append(f"C11={'Y' if c11 else 'N'}({c11_detail})")
    if c13_detail:
        detail_parts.append(f"C13={'Y' if c13 else 'N'}({c13_detail})")
    if not detail_parts:
        detail_parts.append("C11=N C13=N")

    return {
        "ok": c11 and not c13,
        "c11": c11, "c13": not c13, "c13_raw": c13,
        "detail": " | ".join(detail_parts),
    }


# ---- C12 大戶收 (TDCC) ----

def check_large_shareholder(stock_no: str, df, asset_growth: str = "\u2014") -> dict:
    c12_cfg = CONFIG["conditions"]["C12"]
    if df is None:
        return {"c12": True, "c12_detail": f"無股價資料,預設通過 | 規模={asset_growth}"}
    avail = cache.get("tdcc_avail_dates", lambda: tdcc.available_dates())
    if len(avail) < 2:
        return {"c12": True, "c12_detail": f"無TDCC日期,預設通過 | 規模={asset_growth}"}
    dates = avail[:c12_cfg["weeks_to_check"]]
    pcts = []
    token, uri = "", ""
    for d in dates:
        p, token, uri = tdcc.query(stock_no, d, token, uri)
        if p is not None:
            pcts.append({"date": d, "pct": p})
    if len(pcts) < 2:
        return {"c12": True, "c12_detail": f"僅取得{len(pcts)}筆資料 | 規模={asset_growth}"}
    first, last = pcts[0]["pct"], pcts[-1]["pct"]
    trend = last - first
    c12 = trend >= c12_cfg["trend_min"]
    vals = ", ".join(f'{p["pct"]:.2f}%({p["date"]})' for p in pcts)
    if trend > 0.1:
        trend_label = "上升"
    elif trend >= c12_cfg["trend_min"]:
        trend_label = "穩定"
    else:
        trend_label = "下降"
    return {"c12": c12, "c12_detail": f"{trend_label}({vals}) | 規模={asset_growth}"}


# ---- 資產規模追蹤 ----
def _track_totalassets(ticker: str, current_ta):
    if current_ta is None:
        return None, "—"
    prev_ck = f"yf_ta_prev_{ticker}"
    with _ASSET_LOCK:
        dc = cache.load_disk_cache()
        prev_entry = dc.get(prev_ck)
        prev_ta = prev_entry.get("data") if isinstance(prev_entry, dict) else None
        cache.save_disk_cache({prev_ck: current_ta})
    if prev_ta is None:
        return None, "—"
    if current_ta > prev_ta:
        return prev_ta, "增"
    elif current_ta < prev_ta:
        return prev_ta, "減"
    return prev_ta, "持平"


# ============================================================
#  市場位階 (C14~C17) + 買賣點 (C18~C20)
# ============================================================

# ============================================================
#  攻擊訊號 (C18) — 獨立函式，從 check_position 抽出
# ============================================================

def check_c18_attack(df, ticker_yf: str, c18_cfg: dict) -> tuple[bool, str]:
    """C18 攻擊訊號：60分線黃金交叉 OR 日線帶量紅K"""
    c18 = False
    c18_detail = ""
    
    if ticker_yf:
        try:
            ck60 = f"yf60_{ticker_yf}"
            def _fetch_df60():
                for att in range(_REQ_RETRIES):
                    rate_limiter.wait("yf")
                    try:
                        df60 = yf.download(ticker_yf, period=c18_cfg["yf_60min_period"],
                                           interval="60m", progress=False)
                        if df60 is not None and len(df60) > 0:
                            if isinstance(df60.columns, pd.MultiIndex):
                                df60.columns = [col[0].lower() for col in df60.columns]
                            return df_to_dict(df60)
                    except Exception:
                        pass
                    if att < _REQ_RETRIES - 1:
                        time.sleep((att + 1) * 3)
                return None
            df60_raw = cache.get(ck60, _fetch_df60, skip_none=True)
            df60 = pd.DataFrame(df60_raw) if df60_raw is not None else None
            if df60 is not None and len(df60) > c18_cfg["slow_ma_period_60min"]:
                if isinstance(df60.columns, pd.MultiIndex):
                    df60.columns = [col[0].lower() for col in df60.columns]
                c60 = _safe_np(df60["close"]).flatten()
                min_len = c18_cfg["slow_ma_period_60min"]
                if len(c60) >= min_len:
                    fast_ma = pd.Series(c60).rolling(c18_cfg["fast_ma_period_60min"]).mean().to_numpy()
                    slow_ma = pd.Series(c60).rolling(c18_cfg["slow_ma_period_60min"]).mean().to_numpy()
                    if not np.isnan(fast_ma[-1]) and fast_ma[-1] > slow_ma[-1]:
                        c18 = True
                        c18_detail = "60分線黃金交叉"
        except Exception:
            pass
    
    if not c18 and df is not None:
        v = _safe_np(df["volume"])
        c = _safe_np(df["close"])
        o = _safe_np(df["open"])
        vol_ma = pd.Series(v).rolling(c18_cfg["volume_ma_period"]).mean().to_numpy()
        vol_ratio_min = c18_cfg["volume_ratio_min"]
        if not np.isnan(vol_ma[-1]) and c[-1] > o[-1] and v[-1] > vol_ma[-1] * vol_ratio_min:
            c18 = True
            c18_detail = "日線帶量紅K"
    
    return c18, c18_detail


def check_position(df, ticker_yf: str = "", type_label: str = "市值型"):
    """C14~C17 位階 + C18 攻擊訊號 + C19多頭買點 + C20空頭賣點"""
    c14_cfg = CONFIG["conditions"]["C14"]
    c15_cfg = CONFIG["conditions"]["C15"]
    c16_cfg = CONFIG["conditions"]["C16"]
    c17_cfg = CONFIG["conditions"]["C17"]
    c18_cfg = CONFIG["conditions"]["C18"]
    c19_cfg = CONFIG["conditions"]["C19"]
    c20_cfg = CONFIG["conditions"]["C20"]
    ba_cfg = CONFIG["alerts"]["break_ma"]
    bk_cfg = CONFIG["alerts"]["break_k_low"]

    if df is None or len(df) < 250:
        return {"ok": False, "c14": False, "c15": False, "c16": False,
                "c17": False, "c18": False, "c19": False, "c20": False,
                "detail": "資料不足"}

    c = _safe_np(df["close"])
    o = _safe_np(df["open"])
    h = _safe_np(df["high"])
    l = _safe_np(df["low"])
    v = _safe_np(df["volume"])
    y_lookback = 252
    y_high = np.max(c[-y_lookback:]) if len(c) >= y_lookback else np.max(c)
    cur = c[-1]

    # C14: 距60MA 位階（0–5% 範圍，非「回測站回」，type-adjusted）
    # 與 C3 的差異：C3 是「低價碰 20MA」= 短期回測買點；
    # C14 是「收盤距 60MA 的百分比」= 中期位階判斷，proximity 設計更穩健
    # 兩者互補：C3 抓短期支撐，C14 抓中期偏離度
    proximity_max = c14_cfg["proximity_max"]
    ta_c14 = c14_cfg.get("type_adjusted", {})
    if type_label in ta_c14:
        proximity_max = ta_c14[type_label].get("proximity_max", proximity_max)

    ma60 = pd.Series(c).rolling(c14_cfg["ma_period"]).mean().to_numpy()
    if not np.isnan(ma60[-1]) and ma60[-1] > 0:
        dist_from_ma60 = (cur - ma60[-1]) / ma60[-1] * 100
        c14 = c14_cfg["proximity_min"] <= dist_from_ma60 <= proximity_max
    else:
        dist_from_ma60 = None
        c14 = False

    # C15: 連續上漲 (type-adjusted)
    ta_c15 = c15_cfg.get("type_adjusted", {})
    if type_label in ta_c15 and ta_c15[type_label].get("relaxed_mode", False):
        # 寬鬆模式：近 window_days 日中至少 up_days_in_window 日上漲
        window = ta_c15[type_label].get("window_days", 5)
        min_up = ta_c15[type_label].get("up_days_in_window", 3)
        if len(c) >= window + 1:
            up_count = sum(1 for i in range(window) if c[-(i+1)] > c[-(i+2)])
            c15 = up_count >= min_up
        else:
            c15 = False
    else:
        # 標準模式：連續 N 日上漲
        up_days = c15_cfg["consecutive_up_days"]
        if len(c) >= up_days + 1:
            c15 = all(c[-(i+1)] > c[-(i+2)] for i in range(up_days))
        else:
            c15 = False

    # C16: 近 N 日翻倍 (type-adjusted)
    gain_max = c16_cfg["gain_max"]
    lookback_days_c16 = c16_cfg["lookback_days"]
    ta_c16 = c16_cfg.get("type_adjusted", {})
    if type_label in ta_c16:
        gain_max = ta_c16[type_label].get("gain_max", gain_max)
        lookback_days_c16 = ta_c16[type_label].get("lookback_days", lookback_days_c16)

    look16 = min(lookback_days_c16, len(c))
    gain_2m = (c[-1] / c[-look16] - 1) * 100
    doubled = gain_2m >= gain_max

    near_high = cur / y_high >= c17_cfg["near_high_ratio"]
    ma20 = pd.Series(c).rolling(c17_cfg["ma_period"]).mean().to_numpy()
    high_breakdown = near_high and c[-1] < ma20[-1]

    # C18: 攻擊訊號
    c18, c18_detail = check_c18_attack(df, ticker_yf, c18_cfg)

    # C19/C20: KD (ETF 用 k_smooth/d_smooth)
    kd_period = c19_cfg["kd_period"]
    k_smooth = c19_cfg["k_smooth"]
    d_smooth = c19_cfg["d_smooth"]
    k, d = calc_kd(h, l, c, kd_period, k_smooth, d_smooth)
    kd_off = c19_cfg["kd_check_offset"]
    k_prev, d_prev = k[-(kd_off + 1)], d[-(kd_off + 1)]
    k_now, d_now = k[-kd_off], d[-kd_off]
    golden_cross = k_prev <= d_prev and k_now > d_now
    c19 = not np.isnan(ma60[-1]) and c[-1] > ma60[-1] and golden_cross and \
          k_now < c19_cfg["kd_threshold"] and d_now < c19_cfg["kd_threshold"]

    death_cross = k_prev >= d_prev and k_now < d_now
    c20 = not np.isnan(ma60[-1]) and c[-1] < ma60[-1] and death_cross and \
          k_prev > c20_cfg["kd_threshold"] and d_prev > c20_cfg["kd_threshold"]

    # 操作紀律告警
    above_days = ba_cfg["consecutive_above_days"]
    above_ma20_prev = all(c[-d] > ma20[-d] for d in range(1, above_days + 1))
    break_ma = above_ma20_prev and c[-1] < ma20[-1]

    break_k_low = False
    break_k_low_detail = ""
    lookback = bk_cfg["lookback_window"]
    for i in range(min(lookback, len(c) - 2), 1, -1):
        body = c[i] - o[i]
        range_k = h[i] - l[i]
        if range_k == 0:
            continue
        body_ratio = body / range_k
        vol_ma_i = pd.Series(v).rolling(c18_cfg["volume_ma_period"]).mean().to_numpy()
        if not np.isnan(vol_ma_i[i]) and body > 0 and \
           body_ratio > bk_cfg["body_ratio_min"] and \
           v[i] > vol_ma_i[i] * bk_cfg["volume_ratio_min"]:
            if c[-1] < l[i]:
                break_k_low = True
                idx_date = df.index[i].strftime("%m/%d")
                break_k_low_detail = f"跌破{idx_date}低點{l[i]:.0f}"
            break

    ok_pos = c14 and c15 and (not doubled) and (not high_breakdown)
    ok = ok_pos and c18 and (not c20)

    dist_str = f"{dist_from_ma60:+.1f}%" if dist_from_ma60 is not None else "N/A"
    detail_parts = [
        f"C14(距60MA {dist_str} 在{c14_cfg['proximity_min']:.0f}~{proximity_max:.0f}%)={'Y' if c14 else 'N'}",
        f"C15(連{c15_cfg['consecutive_up_days']}日漲)={'Y' if c15 else 'N'}",
        f"C16(翻倍>={gain_max:.0f}%)={'Y' if doubled else 'N'}",
        f"C17(高檔破月線)={'Y' if high_breakdown else 'N'}",
        f"C18(攻擊訊)={'Y' if c18 else 'N'}({c18_detail or '無'})",
        f"C19(多頭買點)={'Y' if c19 else 'N'}",
        f"C20(空頭賣點)={'Y' if c20 else 'N'}",
    ]
    if ok_pos:
        detail_parts.append("安全位階")
    else:
        detail_parts.append("位階不合格")
    detail_parts.append(f"{'+ 攻擊' if c18 else '+ 無攻擊'}")

    return {"ok": ok,
            "c14": c14, "c15": c15, "c16": not doubled, "c17": not high_breakdown,
            "c18": c18, "c18_detail": c18_detail,
            "c19": c19, "c20": c20,
            "break_ma": break_ma, "break_k_low": break_k_low,
            "break_k_low_detail": break_k_low_detail,
            "_dd": dist_str, "_rp": round(0.0 if not c15 else 1.0, 1),
            "detail": " ".join(detail_parts)}


# ============================================================
#  主篩選流程
# ============================================================

def _build_result(ticker: str, type_label: str, tech: dict, c5: dict, c6: dict, c7: dict, c8: dict,
                   chip: dict, ls: dict, pos: dict, df=None) -> ScreeningResult:
    """從各項檢查結果計算得分 + 分類 (ETF 版)"""
    c6_manual = c6.get("manual", False)
    c7_manual = c7.get("manual", False)
    c8_manual = c8.get("manual", False)

    conditions = {
        "c1": tech.get("c1", False), "c2": tech.get("c2", False),
        "c3": tech.get("c3", False), "c4": tech.get("c4", False),
        "c5": c5.get("ok", False),
        "c6": c6.get("ok", False) or c6_manual,
        "c7": c7.get("ok", False) or c7_manual,
        "c8": c8.get("ok", False) or c8_manual,
        "c11": chip.get("c11", False), "c12": ls.get("c12", True),
        "c13": chip.get("c13", True),
        "c14": pos.get("c14", False), "c15": pos.get("c15", False),
        "c16": pos.get("c16", True), "c17": pos.get("c17", True), "c18": pos.get("c18", False),
        "c19": pos.get("c19", False), "c20": pos.get("c20", False),
    }

    weights = CONFIG.get("scoring_weights", ETF_SCORE_WEIGHTS)
    rules = CONFIG.get("hard_reject_rules", {"c16": False, "c20": True})
    tier_th = CONFIG.get("tier_thresholds", {"enter_min": 75, "watch_min": 40})
    exit_params = CONFIG.get("exit_params", {
        "ma20_period": 20, "ma60_period": 60,
        "lookback_days_e1": 30, "lookback_days_e2": 40,
        "high_ratio_e2": 0.95, "volume_ratio_e4": 0.7,
    })

    score = calc_score(conditions, weights)
    hard_rejected = check_hard_reject(conditions, rules)
    exit_sigs = check_exit(df, pos, params=exit_params) if df is not None else []

    tier = classify_tier(
        score, hard_rejected,
        c1=conditions["c1"], c14=conditions["c14"], c15=conditions["c15"],
        exit_signals=exit_sigs,
        thresholds=tier_th,
    )

    score_parts = []
    for key, weight in weights.items():
        actual_key = key.replace("_bonus", "")
        status = "✅" if conditions.get(actual_key, False) else "❌"
        score_parts.append(f"{key}={status}{weight}")

    manual_warnings = []
    if c6_manual:
        manual_warnings.append("C6=⚠無成分股資料需手動查")
    if c7_manual:
        manual_warnings.append("C7=⚠需手動查可分配收益")
    if c8_manual:
        manual_warnings.append("C8=⚠需手動查填息天數")
    if manual_warnings:
        score_parts.append("手動確認:" + ",".join(manual_warnings))

    detail_score = " ".join(score_parts)

    return ScreeningResult(
        ticker=ticker, type_label=type_label, tier=tier, score=score,
        hard_rejected=hard_rejected,
        **{k: conditions.get(k, False) for k in ["c1","c2","c3","c4","c5","c6","c7","c8","c11","c12","c13","c14","c15","c16","c18","c19","c20"]},
        exit_signals=", ".join(exit_sigs),
        close=tech.get("close", 0.0), ma20=tech.get("ma20", 0.0), ma60=tech.get("ma60", 0.0),
        vol_ratio=tech.get("vol_ratio", 0.0),
        detail_score=detail_score,
    )


def screen_one(stock_id: str) -> tuple[dict, ScreeningResult]:
    ticker = stock_id.replace(".TW", "")
    cached = _RESOLVED_ETF_TICKERS.get(stock_id)
    if cached:
        ticker_yf = cached
    else:
        exchange = get_exchange(ticker)
        ticker_yf = f"{ticker}.{exchange}"

    print(f"\n{'='*55}")
    print(f"  ETF 🔍 {ticker}")

    df = _fetch_price(ticker_yf)
    info = _fetch_info(ticker_yf)
    type_label = classify_etf(ticker, info)
    print(f"  ({type_label})")
    print(f"{'='*55}")

    tech = check_tech(df) if df is not None else {"ok": False, "detail": "無股價"}
    c5 = check_c5(df, info) if df is not None else {"ok": True, "detail": "C5=Y(無股價)"}
    c6 = check_c6(ticker, type_label)
    c7 = check_c7(type_label, info)
    c8 = check_c8(type_label, ticker, df)

    chip_info = get_chip(ticker, df) if df is not None else {}
    total_assets = info.get("totalAssets")
    prev_ta, asset_growth = _track_totalassets(ticker, total_assets)
    chip = check_chip(chip_info, df, prev_ta=prev_ta, current_ta=total_assets)
    ls = check_large_shareholder(ticker, df, asset_growth=asset_growth)
    pos = check_position(df, ticker_yf, type_label) if df is not None else \
          {"ok": False, "c14": False, "c15": False, "c16": False, "c17": False, "c18": False, "detail": "無資料"}

    print(f"  T {tech.get('detail','')}")
    print(f"  F {c5['detail']} | {c6['detail']} | {c7['detail']} | {c8['detail']}")
    print(f"  C {chip['detail']}")
    print(f"  C12(大戶收)={'Y' if ls['c12'] else 'N'}({ls['c12_detail']})")
    print(f"  P {pos['detail']}")

    alerts = []
    if pos.get("c18"):
        alerts.append(f"⚡ 攻擊訊號：{pos.get('c18_detail','')}")
    if pos.get("c19"):
        alerts.append("多頭買點：>60MA + KD金叉(<20)")
    if pos.get("c20"):
        alerts.append("空頭賣點：<60MA + KD死叉(>80)")

    if type_label == "市值型":
        if pos.get("break_k_low"):
            alerts.append(f"破K低：{pos.get('break_k_low_detail','')}")
        alerts.append("越跌越買：跌破關鍵均線勿停損，分批加碼")
    elif type_label == "高股息":
        if pos.get("break_ma"):
            alerts.append("破均線：跌破剛站上的20MA")
        alerts.append("注意除權息：跌破發動紅K低點，停止加碼觀察成分股")
    else:
        if pos.get("break_ma"):
            alerts.append("破均線：跌破剛站上的20MA")
        if pos.get("break_k_low"):
            alerts.append(f"破K低：{pos.get('break_k_low_detail','')}")
        alerts.append("⚠️ 槓桿型嚴禁攤平，嚴格停損")

    for a in alerts:
        print(f"  {a}")

    # 顯示需手動確認的項目
    manual_checks = []
    if c6.get("manual"):
        manual_checks.append("C6 成分股無資料需手動查詢")
    if c7.get("manual"):
        manual_checks.append("C7 可分配收益需手動查詢")
    if c8.get("manual"):
        manual_checks.append("C8 填息天數需手動查詢")
    if manual_checks:
        print(f"  {'#'*50}")
        print(f"  # ⚠️ 以下項目需手動確認，尚未計入評分:")
        for m in manual_checks:
            print(f"  #   - {m}")
        print(f"  {'#'*50}")

    # 得分制 + 三層分類
    result = _build_result(ticker, type_label, tech, c5, c6, c7, c8, chip, ls, pos, df)
    
    tier_colors = {
        TIER_ENTER: Fore.GREEN + Style.BRIGHT,
        TIER_WATCH: Fore.YELLOW + Style.BRIGHT,
        TIER_EXIT:  Fore.RED + Style.BRIGHT,
        TIER_OUT:   Style.DIM,
    }
    tier_emoji = {TIER_ENTER: "🟢", TIER_WATCH: "🟡", TIER_EXIT: "🔴", TIER_OUT: "⚫"}
    
    color = tier_colors.get(result.tier, "")
    print(f"  {color}{tier_emoji.get(result.tier, '')} 得分={result.score} 分類={result.tier}{Style.RESET_ALL}")

    raw = {
        "ticker": ticker, "type_label": type_label,
        "passed": result.tier == TIER_ENTER,
        "tech": tech, "c5": c5, "c6": c6, "c7": c7, "c8": c8,
        "chip": chip, "ls": ls, "pos": pos,
    }
    return raw, result


def _process_one_etf(sid: str, quick: bool) -> tuple[dict, ScreeningResult]:
    ticker = sid.strip().split(".TW")[0]
    cached = _RESOLVED_ETF_TICKERS.get(sid)
    if cached:
        ticker_yf = cached
    else:
        exchange = get_exchange(ticker)
        ticker_yf = f"{ticker}.{exchange}"

    if quick:
        df = _fetch_price(ticker_yf)
        tech = check_tech(df) if df is not None else {"ok": False, "detail": "無股價"}
        # ETF quick mode 只看技術面：ETF 基本面重要性低於個股，
        # 且 C5~C8 多為手動檢查，前期篩掉無意義
        skip_heavy = not tech["ok"]
    else:
        skip_heavy = False

    if skip_heavy:
        info = _fetch_info(ticker_yf)
        type_label = classify_etf(ticker, info)
        c5 = check_c5(df, info) if df is not None else {"ok": True, "detail": "C5=Y(無股價)"}
        c6 = check_c6(ticker, type_label)
        c7 = check_c7(type_label, info)
        c8 = check_c8(type_label, ticker, df)

        chip = {"ok": False, "c11": False, "c13": False, "detail": "略過(技術未過)"}
        ls = {"c12": True, "c12_detail": "略過"}
        pos = {"ok": False, "c14": False, "c15": False, "c16": False,
               "c17": False, "c18": False, "c19": False, "c20": False,
               "break_ma": False, "break_k_low": False, "detail": "略過"}

        result = _build_result(ticker, type_label, tech, c5, c6, c7, c8, chip, ls, pos, df)
        raw = {
            "ticker": ticker, "type_label": type_label, "passed": False,
            "tech": tech, "c5": c5, "c6": c6, "c7": c7, "c8": c8,
            "chip": chip, "ls": ls, "pos": pos,
        }
        return raw, result
    else:
        return screen_one(sid)


def screen_batch(stock_ids: list[str], delay: float = 0, quick: bool = True,
                 max_workers: int = 2) -> tuple[list[dict], list[ScreeningResult]]:
    tdcc.load_from_disk_cache(cache, _CACHE_TTL)

    tickers = []
    for sid in stock_ids:
        cached = _RESOLVED_ETF_TICKERS.get(sid)
        if cached:
            tickers.append(cached)
        else:
            ticker = sid.strip().split(".TW")[0]
            exchange = get_exchange(ticker)
            tickers.append(f"{ticker}.{exchange}")
    batch_prefetch_prices(tickers, cache, rate_limiter, retries=_REQ_RETRIES)

    print(f"\n==> ETF 批次篩選 {len(stock_ids)} 檔 ({max_workers} 執行緒) ...\n")
    results_raw = []
    results_scored = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sid = {}
        for sid in stock_ids:
            future = executor.submit(_process_one_etf, sid, quick=quick)
            future_to_sid[future] = sid

        for future in tqdm(as_completed(future_to_sid), total=len(future_to_sid),
                           desc="ETF 篩選進度", unit="檔"):
            sid = future_to_sid[future]
            try:
                raw, result = future.result()
                results_raw.append(raw)
                results_scored.append(result)
            except Exception as e:
                ticker = sid.strip().split(".TW")[0]
                exchange = get_exchange(ticker)
                tqdm.write(f"  [{ticker}] 錯誤: {e}")
                info = _fetch_info(f"{ticker}.{exchange}")
                tl = classify_etf(ticker, info)
                raw = {"ticker": ticker, "exchange": exchange, "type_label": tl, "passed": False}
                result = _build_result(ticker, tl, {}, {}, {}, {}, {}, {}, {}, {}, None)
                results_raw.append(raw)
                results_scored.append(result)

    tdcc.save_to_disk_cache(cache)
    cache.flush()
    return results_raw, results_scored


def _is_tw_ticker(t: str) -> bool:
    """檢查是否為台股代號（4-6 碼純數字，排除 US/JP/KR/IE/BM 等前綴）"""
    t = t.strip()
    return t.isdigit() and 4 <= len(t) <= 6


def screen_batch_prioritized(stock_ids: list[str], quick: bool = True) -> tuple[list[dict], list[ScreeningResult]]:
    """P4-3: 優先順序篩選 (資產規模優先) + P1-2: 持股預取優化，自動跳過已下市 ETF/ETN"""
    print(f"\n[P4-3] 正在預取得資產規模與持股資訊以進行優先排序與預取...")

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    ticker_assets = []
    all_holding_tickers = set()
    skipped = 0
    resolved = {}

    for sid in stock_ids:
        ticker = sid.strip().split(".TW")[0]

        exchange = get_exchange(ticker)
        ticker_yf = f"{ticker}.{exchange}"
        info = _common_fetch_info(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES)
        if info is None:
            skipped += 1
            print(f"  ⏭️  {ticker}: 跳過（無法取得任何資料，可能已下市）")
            continue

        assets = info.get("totalAssets") or 0
        resolved[sid] = ticker_yf
        ticker_assets.append((sid, assets))

        # P1-2: 只收集台股持股代號（排除 ADR/外國股票）
        yf_h = info.get("topHoldings") if info else None
        if isinstance(yf_h, list):
            for h in yf_h:
                sym = h.get("symbol", "").replace(".TW", "").strip()
                if _is_tw_ticker(sym):
                    all_holding_tickers.add(f"{sym}.TW")

    logging.getLogger("yfinance").setLevel(logging.WARNING)

    ticker_assets.sort(key=lambda x: x[1], reverse=True)
    sorted_ids = [x[0] for x in ticker_assets]

    # P1-2: 並行取得所有 ETF 的 Yahoo Finance 持股（TWO fallback，非 lazy）
    print(f"  [Prefetch] 正在並行取得 {len(resolved)} 檔 ETF 持股資料...")
    _resolved_list = list(resolved.values())

    def _fetch_etf_holdings(ticker_yf: str) -> list:
        ticker_sym = ticker_yf.rsplit(".", 1)[0]
        return fetch_top10_holdings(ticker_sym, cache, rate_limiter, retries=_REQ_RETRIES) or []

    holdings_results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futs = {executor.submit(_fetch_etf_holdings, tid): tid for tid in _resolved_list}
        for fut in as_completed(futs):
            ticker_yf = futs[fut]
            ticker_sym = ticker_yf.rsplit(".", 1)[0]
            try:
                h_list = fut.result()
                if h_list:
                    holdings_results[ticker_sym] = h_list
                    for h in h_list:
                        if _is_tw_ticker(h):
                            all_holding_tickers.add(f"{h}.TW")
            except Exception:
                pass

    print(f"  [Prefetch] 持股取得完成：{len(holdings_results)}/{len(resolved)} 檔有資料")

    if skipped:
        print(f"  ⚠  已跳過 {skipped} 檔下市 ETF/ETN")
    if resolved:
        two_count = sum(1 for v in resolved.values() if v.endswith(".TWO"))
        if two_count:
            print(f"  📌 {two_count} 檔 OTC ETF (使用 .TWO 後綴)")

    all_tickers = list(resolved.values())
    final_prefetch_list = list(set(all_tickers) | all_holding_tickers)
    if final_prefetch_list:
        print(f"  [Prefetch] 正在批次下載 {len(final_prefetch_list)} 檔相關股價 (含持股)...")
        batch_prefetch_prices(final_prefetch_list, cache, rate_limiter, retries=_REQ_RETRIES)

    global _RESOLVED_ETF_TICKERS
    _RESOLVED_ETF_TICKERS = resolved

    print(f"  → 篩選 {len(sorted_ids)} 檔\n")
    return screen_batch(sorted_ids, quick=quick)


# ============================================================
#  輸出
# ============================================================

def _ok(v): return "✅" if v else "❌"

def print_summary(results_raw: list[dict], results_scored: list[ScreeningResult]):
    # 按分類分組
    enters = [r for r in results_scored if r.tier == TIER_ENTER]
    watches = [r for r in results_scored if r.tier == TIER_WATCH]
    exits = [r for r in results_scored if r.tier == TIER_EXIT]
    outs = [r for r in results_scored if r.tier == TIER_OUT]

    print(f"\n\n{'='*65}")
    print(f"  ETF 篩選完成: {len(results_scored)} 檔")
    print(f"  {Fore.GREEN}🟢 ENTER={len(enters)}{Style.RESET_ALL}  {Fore.YELLOW}🟡 WATCH={len(watches)}{Style.RESET_ALL}  {Fore.RED}🔴 EXIT={len(exits)}{Style.RESET_ALL}  {Style.DIM}⚫ OUT={len(outs)}{Style.RESET_ALL}")
    print(f"{'='*65}")

    # ---- ENTER ----
    if enters:
        print(f"\n{Fore.GREEN}{'━'*62}")
        print(f"  🟢 ENTER — 適合進場 (得分≥75 + 核心條件全過)")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in sorted(enters, key=lambda x: -x.score):
            print(f"  {Fore.GREEN}{Style.BRIGHT}{r.ticker}{Style.RESET_ALL} ({r.type_label}) | 得分 {r.score} | 收盤 {r.close} | MA20 {r.ma20} | MA60 {r.ma60}")
    else:
        print(f"\n  🟢 無 ENTER 級 ETF")

    # ---- WATCH ----
    if watches:
        print(f"\n{Fore.YELLOW}{'━'*62}")
        print(f"  🟡 WATCH — 追蹤價值 (得分40-74)")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in sorted(watches, key=lambda x: -x.score):
            print(f"  {Fore.YELLOW}{r.ticker}{Style.RESET_ALL} ({r.type_label}) | 得分 {r.score} | 收盤 {r.close}")
    else:
        print(f"\n  🟡 無 WATCH 級 ETF")

    # ---- EXIT ----
    if exits:
        print(f"\n{Fore.RED}{'━'*62}")
        print(f"  🔴 EXIT — 出場信號")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in exits:
            print(f"  {Fore.RED}{r.ticker}{Style.RESET_ALL} | 得分 {r.score} | 出場: {r.exit_signals}")

    # ---- 詳細報告 (ENTER + WATCH) ----
    detailed = enters + watches
    if detailed:
        print(f"\n{'='*65}")
        print(f"  📋 詳細報告")
        print(f"{'='*65}")

    for r in detailed:
        # 找到對應的 raw 結果
        raw = next((rr for rr in results_raw if rr["ticker"] == r.ticker), None)
        if not raw: continue
        
        print(f"\n{'─'*62}")
        tier_emoji = {TIER_ENTER: "🟢", TIER_WATCH: "🟡"}
        color = Fore.GREEN if r.tier == TIER_ENTER else Fore.YELLOW
        print(f"  {color}{tier_emoji.get(r.tier, '')} {r.ticker}{Style.RESET_ALL} ({r.type_label}) | 得分 {r.score} | 收盤 {r.close} | MA60 {r.ma60}")
        print(f"{'─'*62}")
        print(f"  {'條件':<22} {'結果'} {'得分'}")
        print(f"{'─'*62}")
        for key, weight in ETF_SCORE_WEIGHTS.items():
            actual_key = key.replace("_bonus", "")
            val = getattr(r, actual_key, False)
            got = weight if val else 0
            label_map = {
                "c1": "C1 收>60MA且向上", "c2": "C2 收>20MA", "c3": "C3 前Low<20MA",
                "c4": "C4 量雙重均線", "c5": "C5 溢價門檻", "c6": "C6 成分股季線上",
                "c7": "C7 費用率/可分配", "c8": "C8 填息狀態",
                "c11": "C11 法人挺", "c12": "C12 大戶收", "c13": "C13 非散戶接",
                "c14": "C14 距60MA回測", "c15": "C15 連3日上漲", "c17": "C17 非高檔破月線", "c18": "C18 攻擊訊號",
            }
            label = label_map.get(actual_key, actual_key)
            print(f"  {label:<22} {_ok(val):<6} {got:>2}/{weight}")
        print(f"{'─'*62}")

        # 顯示手動檢查提醒
        raw_c6 = raw.get("c6", {})
        raw_c7 = raw.get("c7", {})
        raw_c8 = raw.get("c8", {})
        if raw_c6.get("manual") or raw_c7.get("manual") or raw_c8.get("manual"):
            print(f"  ⚠️ 以下項目需手動確認（尚未計入評分）:")
            if raw_c6.get("manual"):
                print(f"    - C6 成分股資料")
            if raw_c7.get("manual"):
                print(f"    - C7 可分配收益")
            if raw_c8.get("manual"):
                print(f"    - C8 填息天數")
            print(f"{'─'*62}")


# ============================================================
#  主程式
# ============================================================

def _load_candidates(path: str = "candidates_ETF.csv") -> list[str]:
    import csv
    if not os.path.exists(path):
        print(f"  ⚠ 找不到 {path}，使用預設範例")
        return ["0050", "0056", "00878"]
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["ticker"].strip() for row in reader if row.get("ticker","").strip()]


if __name__ == "__main__":
    CANDIDATES = _load_candidates()

    print("=" * 65)
    print("  ETF 篩選 v4 — 技術+基本面+籌碼+位階+買賣點 (20條件)")
    print(f"  執行: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  候選: {len(CANDIDATES)} 檔")
    print("=" * 65)

    raw_results, scored_results = screen_batch_prioritized(CANDIDATES, quick=True)
    print_summary(raw_results, scored_results)
    
    # 持久化 (P3-3)
    if scored_results:
        path = save_results(scored_results)
        print(f"\n  💾 結果已存至: {path}")

    cache.flush()
    cache.close()
