"""
台股篩選腳本 v4 — 技術(4) + 基本面(5) + 籌碼(3) + 位階(5) + 買賣點(3) = 20條件
============================================================================
資料源: yfinance + TWSE Open API (t187ap05/14, fund/T86, TWT93U) + TDCC 集保
共用模組: common/ (cache, rate_limit, twse, tdcc, yf_utils, kd, serialization)

條件總表:
  C1~C4  技術面買點
  C5~C10 基本面好股 (EPS/負債/營收YoY/存貨) [C8已移除，C6涵蓋]
  C11    法人挺：跌破均線時 外資或投信連續買超
  C12    大戶收：千張大戶比率不減反增
  C13    散戶接排除：法人大賣但融資大增
  C14    60MA回測買點：收盤價距60MA 在 0~5%（站上或微破均線）
  C15    連3日上漲：近3個交易日收盤價連續上漲（回測確認反轉）
  C16    未翻倍排除：1~2月未漲≥80%
  C17    非高檔震排除：非高檔跌破月線
  C18    攻擊訊號：60分線黃金交叉 OR 日線帶量紅K
  C19    多頭買點：>60MA + KD黃金交叉(<20區)
  C20    空頭賣點排除：<60MA + KD死亡交叉(>80區)
  操作紀律 (告警):
    破均線：收盤跌破剛站上的 20MA
    破K低 ：收盤跌破近日發動紅K最低點
"""

import time
import warnings
import logging
import threading
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import numpy as np
import pandas as pd
import os

# ─── 必須在 import yfinance 之前設定 ───
# SSL 憑證路徑 (修復 curl: (77) CAfile 錯誤)
if not os.environ.get("SSL_CERT_FILE"):
    for cp in [
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/etc/ssl/cert.pem",
    ]:
        if os.path.exists(cp):
            os.environ["SSL_CERT_FILE"] = cp
            os.environ["CURL_CA_BUNDLE"] = cp
            os.environ["REQUESTS_CA_BUNDLE"] = cp
            break

# yfinance 內部緩存目錄指向專案 .cache (避免系統目錄權限/鎖定問題)
_yf_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "py-yfinance")
os.makedirs(_yf_cache_dir, exist_ok=True)

import yfinance
# 設定 yfinance 三個 peewee/SQLite 緩存位置 (tz, cookie, isin)
yfinance.set_tz_cache_location(_yf_cache_dir)

# 清理舊的損壞緩存檔 (避免上次中斷留下的鎖)
for fname in ("tkr-tz.db", "tkr-tz.db-wal", "tkr-tz.db-shm",
              "cookies.db", "cookies.db-wal", "cookies.db-shm",
              "isin-tkr.db", "isin-tkr.db-wal", "isin-tkr.db-shm"):
    fpath = os.path.join(_yf_cache_dir, fname)
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
        except Exception:
            pass

# 關鍵修復：為 yfinance 內部 peewee 數據庫設定 busy_timeout，避免多線程 "database is locked"
import yfinance.cache as _yfc
for _mgr_name in ('_TzDBManager', '_CookieDBManager', '_ISINDBManager'):
    _mgr = getattr(_yfc, _mgr_name)
    _db = _mgr.get_database()
    if _db:
        _db.execute_sql('PRAGMA busy_timeout=10000')

# 強制預初始化所有 yfinance 緩存 (避免多線程並發 create_tables)
import yfinance as _yf
try:
    _yf.download("2330.TW", period="5d", progress=False, auto_adjust=True, threads=False)
except Exception:
    pass

from tqdm import tqdm
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

warnings.filterwarnings("ignore", message=".*urllib3.*OpenSSL.*")
warnings.filterwarnings("ignore", category=UserWarning)

from common import (
    load_config, DiskCache, RateLimiter, logger,
    twse_json, twse_data,
    TDCCQuery, batch_prefetch_prices, fetch_price as _fetch_price,
    fetch_info, fetch_financials, get_stock_info, get_exchange,
    calc_kd, to_json_val, df_to_dict, dict_to_df,
    calc_score, check_hard_reject, classify_tier, check_exit,
    ScreeningResult, save_results,
    STOCK_SCORE_WEIGHTS, TIER_ENTER, TIER_WATCH, TIER_EXIT, TIER_OUT,
)
from common.yf_utils import _warm_yf_cache

# ---- 設定檔 ----
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_DEFAULTS = {
    "cache_ttl_seconds": 7200,
    "request_retries": 3,
    "rate_limit": {
        "twse": {"delay": 2.0, "jitter": 0.5},
        "tdcc": {"delay": 3.0, "jitter": 0.5},
        "yf":   {"delay": 1.0, "jitter": 0.3}
    },
    "market_cap_groups": {
        "large": {
            "threshold": 500_000_000_000,
            "label": "大型",
            "rev_yoy_min": 5.0,
            "debt_ratio_max": 60.0,
            "eps_min": 1.0
        },
        "mid": {
            "threshold": 50_000_000_000,
            "label": "中型",
            "rev_yoy_min": 10.0,
            "debt_ratio_max": 50.0,
            "eps_min": 0.5
        },
        "small": {
            "threshold": 0,
            "label": "小型",
            "rev_yoy_min": 15.0,
            "debt_ratio_max": 45.0,
            "eps_min": 0.0
        },
        "default": {
            "label": "未知",
            "rev_yoy_min": 10.0,
            "debt_ratio_max": 50.0,
            "eps_min": 0.5
        }
    },
    "scoring_weights": {
        "c1": 10, "c2": 5, "c3": 5, "c4": 10,
        "c5": 5, "c6": 5, "c7": 10, "c9": 5, "c10": 5,
        "c11": 10, "c12": 5, "c13": 5,
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
        "C4":  {"volume_ma_period": 5, "volume_ratio_min": 1.0},
        "C5":  {"eps_min": 0.0},  # 已由 market_cap_groups 覆蓋，此值不生效
        "C6":  {"debt_ratio_max": 50.0},
        "C7":  {"rev_yoy_min": 10.0},
        # C8 已移除（C6 負債比<50% 已涵蓋，C8 負債比<60% 冗餘）
        "C9":  {"inv_days_max": 365},
        "C10": {"sigma_multiplier": 2.0},
        "C11": {"ma_period": 20, "lookback_window": 20, "consecutive_days": 3},
        "C12": {"large_share_threshold": 1000000, "weeks_to_check": 3, "trend_min": -0.5},
        "C13": {"lookback_days": 3},
        "C14": {"ma_period": 60, "proximity_min": 0.0, "proximity_max": 5.0},  # 5%上限=回測進場；強趨勢股(>5%)只達WATCH
        "C15": {"consecutive_up_days": 3},
        "C16": {"lookback_days": 40, "gain_max": 80.0},
        "C17": {"near_high_ratio": 0.85, "ma_period": 20},
        "C18": {"fast_ma_period_60min": 20, "slow_ma_period_60min": 60, "volume_ma_period": 5, "volume_ratio_min": 1.2, "yf_60min_period": "1mo"},
        "C19": {"ma_period": 60, "kd_period": 9, "kd_threshold": 20, "kd_check_offset": 1},
        "C20": {"ma_period": 60, "kd_period": 9, "kd_threshold": 80, "kd_check_offset": 1}
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
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_CACHE_DB = os.path.join(_CACHE_DIR, "tw_quant.db")
_CACHE_TTL = CONFIG["cache_ttl_seconds"]

cache = DiskCache(_CACHE_DB, ttl=_CACHE_TTL)
rate_limiter = RateLimiter(CONFIG["rate_limit"])

# screen_batch_prioritized 解析的 {sid → yahoo_ticker} 映射，供 screen_batch 使用
_RESOLVED_TICKERS: dict[str, str] = {}
tdcc = TDCCQuery(rate_limiter, retries=_REQ_RETRIES,
                 large_share_threshold=CONFIG["conditions"]["C12"]["large_share_threshold"])


# ---- 價格取得 (包裝 common) ----
def fetch_price(ticker_yf: str, days: int = 400):
    return _fetch_price(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES, days=days)


# ---- TWSE API (包裝 common) ----
_T86_CACHE = {}
_T93_CACHE = {}
_TWSE_LOCK = threading.Lock()


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

def check_tech(df) -> dict:
    c1_cfg = CONFIG["conditions"]["C1"]
    c2_cfg = CONFIG["conditions"]["C2"]
    c3_cfg = CONFIG["conditions"]["C3"]
    c4_cfg = CONFIG["conditions"]["C4"]

    c = df["close"].to_numpy()
    l = df["low"].to_numpy()
    v = df["volume"].to_numpy()
    ma20_c2 = pd.Series(c).rolling(c2_cfg["ma_period"]).mean().to_numpy()
    ma20_c3 = pd.Series(c).rolling(c3_cfg["ma_period"]).mean().to_numpy()
    ma60 = pd.Series(c).rolling(c1_cfg["ma_period"]).mean().to_numpy()
    v_ma = pd.Series(v).rolling(c4_cfg["volume_ma_period"]).mean().to_numpy()
    i = -1

    trend_ok = not np.isnan(ma60[i - c1_cfg["trend_check_days"]]) and \
               ma60[i] > ma60[i - c1_cfg["trend_check_days"]]
    c1 = c[i] > ma60[i] and trend_ok
    c2 = c[i] > ma20_c2[i]
    # C3: 回測 20MA（近 N 日低價曾觸及月線）
    # 與 C14 的差異：C3 是「低價碰月線」= 回測站回，C14 是「收盤距季線 X%」= 位階判斷
    # C3 用低價觸及、lookback 短期（5日），C14 用收盤距離、MA60 長期（60日）
    c3l = c3_cfg["lookback_days"]
    c3 = any(
        (l[i - d] < ma20_c3[i - d] if not np.isnan(ma20_c3[i - d]) else False)
        for d in range(1, c3l + 1)
    )
    c4 = v[i] > v_ma[i]

    return {
        "ok": c1 and c2 and c3 and c4,
        "c1": c1, "c2": c2, "c3": c3, "c4": c4,
        "close": round(c[i], 1), "ma20": round(ma20_c2[i], 1), "ma60": round(ma60[i], 1),
        "vol_ratio": round(v[i] / v_ma[i], 2) if v_ma[i] > 0 else 0,
        "df": df,
        "detail": f"C1={'Y' if c1 else 'N'} C2={'Y' if c2 else 'N'} C3={'Y' if c3 else 'N'} C4={'Y' if c4 else 'N'}",
    }


# ============================================================
#  基本面 (C5~C10)
# ============================================================

def get_fund(stock_id: str) -> dict:
    info = {"eps": None, "debt_ratio": None, "rev_yoy": None, "market_cap": None,
            "inv_days": None, "inv_days_mean": None, "inv_days_std": None}
    # C5 EPS (快取)
    eps_all = cache.get("eps_all", lambda: twse_json(
        "https://openapi.twse.com.tw/v1/opendata/t187ap14_L", rate_limiter=rate_limiter) or [])
    if eps_all:
        for r in eps_all:
            if r.get("公司代號") == stock_id:
                try:
                    info["eps"] = float(r["基本每股盈餘(元)"])
                except (ValueError, KeyError, TypeError):
                    logger.warning("[%s] EPS 解析失敗: %s", stock_id, r.get("基本每股盈餘(元)"))
                break
    # C7 月營收 YoY (快取)
    rev_all = cache.get("rev_all", lambda: twse_json(
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L", rate_limiter=rate_limiter) or [])
    if rev_all:
        for r in rev_all:
            if r.get("公司代號") == stock_id:
                try:
                    info["rev_yoy"] = float(r["營業收入-去年同月增減(%)"])
                except (ValueError, KeyError, TypeError):
                    logger.warning("[%s] 營收 YoY 解析失敗: %s", stock_id, r.get("營業收入-去年同月增減(%)"))
                break
    # 負債比 & 存貨 (yfinance，含 .TWO + 資產負債表備援)
    try:
        ticker_yf = f"{stock_id}.TW"
        yinfo = get_stock_info(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES)
        info["market_cap"] = yinfo.get("marketCap")

        fins = fetch_financials(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES)
        bs, fin = fins["bs"], fins["fin"]

        if bs is not None and not bs.empty:
            if "Total Liabilities Net Minority Interest" in bs.index and "Total Assets" in bs.index:
                liab = bs.loc["Total Liabilities Net Minority Interest"].dropna()
                assets = bs.loc["Total Assets"].dropna()
                if not liab.empty and not assets.empty:
                    info["debt_ratio"] = round((liab.iloc[0] / assets.iloc[0]) * 100, 1)
            if "Inventory" in bs.index:
                inv = bs.loc["Inventory"].dropna()
                cogs_label = "Cost Of Revenue" if (fin is not None and "Cost Of Revenue" in fin.index) else \
                             ("Reconciled Cost Of Revenue" if (fin is not None and "Reconciled Cost Of Revenue" in fin.index) else None)
                if cogs_label and fin is not None:
                    cogs = fin.loc[cogs_label].dropna()
                    days = []
                    cogs_dates = sorted(cogs.index)
                    inv_dates = sorted(inv.index)
                    if len(cogs_dates) > 1:
                        avg_gap = (cogs_dates[-1] - cogs_dates[0]).days / len(cogs_dates)
                        divisor = 365 if avg_gap > 200 else 91.25
                    else:
                        divisor = 91.25
                    for d in inv_dates:
                        if d in cogs.index and cogs.loc[d] > 0:
                            days.append(inv.loc[d] / (cogs.loc[d] / divisor))
                    if days:
                        info["inv_days"] = round(days[0], 1)
                        info["inv_days_mean"] = round(np.mean(days), 1)
                        info["inv_days_std"] = round(np.std(days), 1) if len(days) > 1 else 0
    except KeyError as e:
        logger.warning("[%s] 財報格式異常 (缺少欄位 %s)", stock_id, e)
    except (ValueError, TypeError) as e:
        logger.warning("[%s] 財報數值解析失敗: %s", stock_id, e)
    except Exception as e:
        logger.error("[%s] 財報讀取失敗: %s", stock_id, e)
    return info


def check_fund(info: dict) -> dict:
    """基本面檢查：根據市值分群套用不同門檻 (P2-3)"""
    mc = info.get("market_cap")
    mc_groups = CONFIG.get("market_cap_groups", _DEFAULTS["market_cap_groups"])
    
    # 決定門檻
    if mc is None:
        cfg = mc_groups["default"]
    elif mc > mc_groups["large"]["threshold"]:
        cfg = mc_groups["large"]
    elif mc > mc_groups["mid"]["threshold"]:
        cfg = mc_groups["mid"]
    else:
        cfg = mc_groups["small"]

    ry_min = cfg["rev_yoy_min"]
    dr_max = cfg["debt_ratio_max"]
    ep_min = cfg["eps_min"]
    mc_label = cfg["label"]

    c9_cfg = CONFIG["conditions"]["C9"]
    c10_cfg = CONFIG["conditions"]["C10"]

    ep, dr, ry = info["eps"], info["debt_ratio"], info["rev_yoy"]
    inv, im, is_ = info["inv_days"], info["inv_days_mean"], info["inv_days_std"]

    c5 = ep is not None and ep > ep_min
    c6 = dr is not None and dr < dr_max
    c7 = ry is not None and ry > ry_min
    c9 = inv is not None and inv < c9_cfg["inv_days_max"]
    if inv is not None and im is not None and is_ is not None and is_ > 0:
        c10 = abs(inv - im) <= c10_cfg["sigma_multiplier"] * is_
    else:
        c10 = False   # 無 sigma 資料 → 保守不給過

    parts = []
    parts.append(f"規模={mc_label}")
    if ry is not None: parts.append(f"YoY={ry:.1f}%")
    if dr is not None: parts.append(f"負債={dr}%")
    if inv is not None: parts.append(f"存貨={inv}d")
    detail = " | ".join(parts) if parts else "無資料"
    return {"ok": c5 and c6 and c7 and c9 and c10,
            "c5": c5, "c6": c6, "c7": c7, "c9": c9, "c10": c10,
            "detail": detail,
            **info}


# ============================================================
#  籌碼面 (C11~C13)
# ============================================================

def get_chip(stock_id: str, df) -> dict:
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


def check_chip(info: dict, df) -> dict:
    c11_cfg = CONFIG["conditions"]["C11"]
    c13_cfg = CONFIG["conditions"]["C13"]

    c11 = c13 = False
    c11_detail = c13_detail = ""
    if df is None or not info.get("foreign_net"):
        return {"ok": False, "c11": False, "c13": False, "detail": "無籌碼資料"}

    c = df["close"].to_numpy()
    ma20 = pd.Series(c).rolling(c11_cfg["ma_period"]).mean().to_numpy()
    fn = info["foreign_net"]
    tn = info["trust_net"]
    mb = info["margin_balance"]
    chip_dates = info.get("dates", [])
    margin_dates = info.get("margin_dates", [])

    # C11: 找出近 N 日中收盤<MA20 的區間, 檢查對應日期外資或投信是否連續買超
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

    # C13: 法人大賣但融資大增（含自營商）
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

    detail_parts = []
    if c11_detail:
        detail_parts.append(f"C11={'Y' if c11 else 'N'}({c11_detail})")
    if c13_detail:
        detail_parts.append(f"C13={'Y' if c13 else 'N'}({c13_detail})")
    if not detail_parts:
        detail_parts.append("C11=N C13=N")

    return {
        "ok": c11 and not c13,
        "c11": c11, "c13": not c13,
        "c13_raw": c13,
        "detail": " | ".join(detail_parts),
    }


# ---- C12 大戶收 (TDCC) ----

def check_large_shareholder(stock_no: str, df) -> dict:
    """C12 大戶收：回檔期間千張大戶比率不減反增"""
    c12_cfg = CONFIG["conditions"]["C12"]

    if df is None:
        return {"c12": True, "c12_detail": "無股價資料,預設通過"}

    avail = cache.get("tdcc_avail_dates", lambda: tdcc.available_dates())
    if len(avail) < 2:
        return {"c12": True, "c12_detail": "無TDCC日期,預設通過"}
    dates = avail[:c12_cfg["weeks_to_check"]]

    pcts = []
    token, uri = "", ""
    for d in dates:
        p, token, uri = tdcc.query(stock_no, d, token, uri)
        if p is not None:
            pcts.append({"date": d, "pct": p})

    if len(pcts) < 2:
        return {"c12": True, "c12_detail": f"僅取得{len(pcts)}筆資料"}

    first, last = pcts[0]["pct"], pcts[-1]["pct"]
    trend = last - first
    trend_min = c12_cfg["trend_min"]
    c12 = trend >= trend_min
    vals = ", ".join(f'{p["pct"]:.2f}%({p["date"]})' for p in pcts)
    if trend > 0.1:
        trend_label = "上升"
    elif trend >= trend_min:
        trend_label = "穩定"
    else:
        trend_label = "下降"
    c12_detail = f"{trend_label}({vals})"

    return {"c12": c12, "c12_detail": c12_detail}


# ============================================================
#  市場位階 (C14~C17) + 買賣點 (C18~C20)
# ============================================================

# ============================================================
#  攻擊訊號 (C18) — 獨立函式，從 check_position 抽出
# ============================================================

def check_c18_attack(df, ticker_yf: str, c18_cfg: dict) -> tuple[bool, str]:
    """C18 攻擊訊號：60分線黃金交叉 OR 日線帶量紅K
    
    從 check_position 抽出，職責分離：
    - 60分線下載 + 快取
    - 黃金交叉判定
    - 回退至日線帶量紅K
    """
    c18 = False
    c18_detail = ""
    
    # 60分線黃金交叉
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
                c60 = df60["close"].to_numpy().flatten()
                min_len = c18_cfg["slow_ma_period_60min"]
                if len(c60) >= min_len:
                    fast_ma = pd.Series(c60).rolling(c18_cfg["fast_ma_period_60min"]).mean().to_numpy()
                    slow_ma = pd.Series(c60).rolling(c18_cfg["slow_ma_period_60min"]).mean().to_numpy()
                    if not np.isnan(fast_ma[-1]) and fast_ma[-1] > slow_ma[-1]:
                        c18 = True
                        c18_detail = "60分線黃金交叉"
        except Exception:
            pass
    
    # 回退：日線帶量紅K
    if not c18 and df is not None:
        v = df["volume"].to_numpy()
        c = df["close"].to_numpy()
        o = df["open"].to_numpy()
        vol_ma = pd.Series(v).rolling(c18_cfg["volume_ma_period"]).mean().to_numpy()
        vol_ratio_min = c18_cfg["volume_ratio_min"]
        if not np.isnan(vol_ma[-1]) and c[-1] > o[-1] and v[-1] > vol_ma[-1] * vol_ratio_min:
            c18 = True
            c18_detail = "日線帶量紅K"
    
    return c18, c18_detail


def check_position(df, ticker_yf: str = "") -> dict:
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

    c = df["close"].to_numpy()
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    v = df["volume"].to_numpy()
    y_lookback = 252
    y_high = np.max(c[-y_lookback:]) if len(c) >= y_lookback else np.max(c)
    cur = c[-1]

    # C14: 距60MA 位階（0–5% 範圍，非「回測站回」）
    # 與 C3 的差異：C3 是「低價碰 20MA」= 短期回測買點；
    # C14 是「收盤距 60MA 的百分比」= 中期位階判斷，proximity 設計更穩健
    # 兩者互補：C3 抓短期支撐，C14 抓中期偏離度
    ma60 = pd.Series(c).rolling(c14_cfg["ma_period"]).mean().to_numpy()
    if not np.isnan(ma60[-1]) and ma60[-1] > 0:
        dist_from_ma60 = (cur - ma60[-1]) / ma60[-1] * 100
        c14 = c14_cfg["proximity_min"] <= dist_from_ma60 <= c14_cfg["proximity_max"]
    else:
        dist_from_ma60 = None
        c14 = False

    # C15: 連3日上漲
    up_days = c15_cfg["consecutive_up_days"]
    if len(c) >= up_days + 1:
        c15 = all(c[-(i+1)] > c[-(i+2)] for i in range(up_days))
    else:
        c15 = False

    # C16: 近 N 日翻倍
    look16 = min(c16_cfg["lookback_days"], len(c))
    gain_2m = (c[-1] / c[-look16] - 1) * 100
    doubled = gain_2m >= c16_cfg["gain_max"]

    # C17: 高檔震盪跌破月線
    near_high = cur / y_high >= c17_cfg["near_high_ratio"]
    ma20 = pd.Series(c).rolling(c17_cfg["ma_period"]).mean().to_numpy()
    high_breakdown = near_high and c[-1] < ma20[-1]

    # C18: 攻擊訊號 (60分線黃金交叉 OR 日線帶量紅K)
    c18, c18_detail = check_c18_attack(df, ticker_yf, c18_cfg)

    # C19: 多頭買點
    kd_period = c19_cfg["kd_period"]
    k, d = calc_kd(h, l, c, kd_period)
    kd_off = c19_cfg["kd_check_offset"]
    k_prev, d_prev = k[-(kd_off + 1)], d[-(kd_off + 1)]
    k_now, d_now = k[-kd_off], d[-kd_off]
    golden_cross = k_prev <= d_prev and k_now > d_now
    c19 = not np.isnan(ma60[-1]) and c[-1] > ma60[-1] and golden_cross and \
          k_now < c19_cfg["kd_threshold"] and d_now < c19_cfg["kd_threshold"]

    # C20: 空頭賣點
    death_cross = k_prev >= d_prev and k_now < d_now
    c20 = not np.isnan(ma60[-1]) and c[-1] < ma60[-1] and death_cross and \
          k_prev > c20_cfg["kd_threshold"] and d_prev > c20_cfg["kd_threshold"]

    # ---- 操作紀律告警 ----
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

    # 位階安全
    ok_pos = c14 and c15 and (not doubled) and (not high_breakdown)
    ok = ok_pos and c18 and (not c20)

    dist_str = f"{dist_from_ma60:+.1f}%" if dist_from_ma60 is not None else "N/A"
    detail_parts = [
        f"C14(距60MA {dist_str} 在{c14_cfg['proximity_min']:.0f}~{c14_cfg['proximity_max']:.0f}%)={'Y' if c14 else 'N'}",
        f"C15(連{c15_cfg['consecutive_up_days']}日漲)={'Y' if c15 else 'N'}",
        f"C16(翻倍≥{c16_cfg['gain_max']:.0f}%)={'Y' if doubled else 'N'}",
        f"C17(高檔破月線)={'Y' if high_breakdown else 'N'}",
        f"C18(攻擊訊)={'Y' if c18 else 'N'}({c18_detail or '無'})",
        f"C19(多頭買點)={'Y' if c19 else 'N'}",
        f"C20(空頭賣點)={'Y' if c20 else 'N'}",
    ]
    if ok_pos:
        detail_parts.append("✅安全位階")
    else:
        detail_parts.append("❌位階不合格")
    detail_parts.append(f"{'+ 攻擊' if c18 else '+ 無攻擊'}")

    return {"ok": ok,
            "c14": c14, "c15": c15, "c16": not doubled, "c17": not high_breakdown,
            "c18": c18, "c18_detail": c18_detail,
            "c19": c19, "c20": c20,
            "break_ma": break_ma, "break_k_low": break_k_low,
            "break_k_low_detail": break_k_low_detail,
            "_dd": dist_str, "_rp": round(0.0 if not c15 else 1.0, 1),
            "detail": " ".join(detail_parts)}


def _resolve_ticker_suffix(ticker: str) -> tuple[str, str]:
    """回傳 (yf_ticker, exchange) — 優先使用 _RESOLVED_TICKERS，否則查 twstock.codes"""
    exchange = get_exchange(ticker)
    return f"{ticker}.{exchange}", exchange


# ============================================================
#  主篩選流程
# ============================================================

def _build_result(ticker: str, tech: dict, fund: dict, chip: dict, ls: dict, pos: dict,
                   df=None, skipped: bool = False, exchange: str = "") -> ScreeningResult:
    """從各項檢查結果計算得分 + 分類"""
    # 收集條件通過狀態
    conditions = {
        "c1": tech.get("c1", False),
        "c2": tech.get("c2", False),
        "c3": tech.get("c3", False),
        "c4": tech.get("c4", False),
        "c5": fund.get("c5", False),
        "c6": fund.get("c6", False),
        "c7": fund.get("c7", False),
        "c9": fund.get("c9", False),
        "c10": fund.get("c10", False),
        "c11": chip.get("c11", False),
        "c12": ls.get("c12", True),
        "c13": chip.get("c13", True),   # True = 安全（非散戶接）
        "c14": pos.get("c14", False),
        "c15": pos.get("c15", False),
        "c16": pos.get("c16", True),   # True = 安全（未翻倍）
        "c17": pos.get("c17", True),   # True = 安全（未高檔跌破月線）
        "c18": pos.get("c18", False),
        "c19": pos.get("c19", False),
        "c20": pos.get("c20", False),  # True = 空頭賣點觸發
    }

    # P2-2: 使用設定檔中的權重與規則
    weights = CONFIG.get("scoring_weights", STOCK_SCORE_WEIGHTS)
    rules = CONFIG.get("hard_reject_rules", {"c16": False, "c20": True})
    tier_th = CONFIG.get("tier_thresholds", {"enter_min": 75, "watch_min": 40})
    exit_params = CONFIG.get("exit_params", {
        "ma20_period": 20, "ma60_period": 60,
        "lookback_days_e1": 30, "lookback_days_e2": 40,
        "high_ratio_e2": 0.95, "volume_ratio_e4": 0.7,
    })
    
    score = calc_score(conditions, weights)
    hard_rejected = check_hard_reject(conditions, rules)

    # 出場信號
    exit_sigs = check_exit(df, pos, params=exit_params) if df is not None else []

    tier = classify_tier(
        score, hard_rejected,
        c1=conditions["c1"], c14=conditions["c14"], c15=conditions["c15"],
        exit_signals=exit_sigs,
        thresholds=tier_th,
    )

    # 得分明細
    score_parts = []
    for key, weight in weights.items():
        actual_key = key.replace("_bonus", "")
        status = "✅" if conditions.get(actual_key, False) else "❌"
        score_parts.append(f"{key}={status}{weight}")
    detail_score = " ".join(score_parts)

    return ScreeningResult(
        ticker=ticker,
        exchange=exchange,
        tier=tier,
        score=score,
        hard_rejected=hard_rejected,
        **{k: conditions.get(k, False) for k in ["c1","c2","c3","c4","c5","c6","c7","c9","c10","c11","c12","c13","c14","c15","c16","c18","c19","c20"]},
        exit_signals=", ".join(exit_sigs),
        close=tech.get("close", 0.0),
        ma20=tech.get("ma20", 0.0),
        ma60=tech.get("ma60", 0.0),
        vol_ratio=tech.get("vol_ratio", 0.0),
        detail_score=detail_score,
    )


def screen_one(stock_id: str) -> tuple[dict, ScreeningResult]:
    ticker = stock_id.replace(".TW", "")

    cached = _RESOLVED_TICKERS.get(stock_id)
    if cached:
        ticker_yf = cached
        exchange = "TWO" if cached.endswith(".TWO") else "TW"
    else:
        ticker_yf, exchange = _resolve_ticker_suffix(ticker)

    print(f"\n{'='*55}")
    print(f"  🔍 {ticker}")
    print(f"{'='*55}")

    df = fetch_price(ticker_yf)
    tech = check_tech(df) if df is not None else {"ok": False, "detail": "無股價"}
    finfo = get_fund(ticker)
    fund = check_fund(finfo)
    chip_info = get_chip(ticker, df) if df is not None else {}
    chip = check_chip(chip_info, df)
    ls = check_large_shareholder(ticker, df) if df is not None else {"c12": True, "c12_detail": "無資料"}
    pos = check_position(df, ticker_yf) if df is not None else \
          {"ok": False, "c14": False, "c15": False, "c16": False, "c17": False, "c18": False, "c19": False, "c20": False, "detail": "無資料"}

    print(f"  T {tech.get('detail','')}")
    print(f"  F {fund['detail']}")
    print(f"  C {chip['detail']}")
    print(f"  C12(大戶收)={'Y' if ls['c12'] else 'N'}({ls['c12_detail']})")
    print(f"  P {pos['detail']}")

    alerts = []
    if pos.get("c18"):
        alerts.append(f"⚡ 攻擊訊號：{pos.get('c18_detail', '')}")
    if pos.get("c19"):
        alerts.append("⚠️ 多頭買點：>60MA + KD金叉(<20)")
    if pos.get("c20"):
        alerts.append("🔴 空頭賣點：<60MA + KD死叉(>80)")
    if pos.get("break_ma"):
        alerts.append("🛑 破均線：跌破剛站上的20MA")
    if pos.get("break_k_low"):
        alerts.append(f"🛑 破K低：{pos.get('break_k_low_detail','')}")
    for a in alerts:
        print(f"  {a}")

    # 得分制 + 三層分類
    result = _build_result(ticker, tech, fund, chip, ls, pos, df, exchange=exchange)

    tier_colors = {
        TIER_ENTER: Fore.GREEN + Style.BRIGHT,
        TIER_WATCH: Fore.YELLOW + Style.BRIGHT,
        TIER_EXIT:  Fore.RED + Style.BRIGHT,
        TIER_OUT:   Style.DIM,
    }
    tier_emoji = {TIER_ENTER: "🟢", TIER_WATCH: "🟡", TIER_EXIT: "🔴", TIER_OUT: "⚫"}
    
    color = tier_colors.get(result.tier, "")
    print(f"  {color}{tier_emoji.get(result.tier, '')} 得分={result.score} 分類={result.tier}{Style.RESET_ALL}", end="")
    if result.hard_rejected:
        print(f" {Fore.RED}(硬淘汰){Style.RESET_ALL}", end="")
    if result.exit_signals:
        print(f" {Fore.RED}出場={result.exit_signals}{Style.RESET_ALL}", end="")
    print()

    raw = {
        "ticker": ticker,
        "exchange": exchange,
        "passed": result.tier == TIER_ENTER,
        "tech": tech, "fund": fund, "chip": chip, "ls": ls, "pos": pos,
    }
    return raw, result


def _process_one_stock(sid: str, quick: bool) -> tuple[dict, ScreeningResult]:
    ticker = sid.strip().split(".TW")[0]
    cached = _RESOLVED_TICKERS.get(sid)
    if cached:
        ticker_yf = cached
        exchange = "TWO" if cached.endswith(".TWO") else "TW"
    else:
        ticker_yf, exchange = _resolve_ticker_suffix(ticker)

    if quick:
        df = fetch_price(ticker_yf)
        tech = check_tech(df) if df is not None else {"ok": False, "detail": "無股價"}
        finfo = get_fund(ticker)
        fund = check_fund(finfo)
        skip_expensive = not (tech["ok"] and fund["ok"])
    else:
        skip_expensive = False

    if skip_expensive:
        chip = {"ok": False, "c11": False, "c13": False, "detail": "略過(前項未過)"}
        ls = {"c12": True, "c12_detail": "略過"}
        pos = {"ok": False, "c14": False, "c15": False, "c16": False,
               "c17": False, "c18": False, "c19": False, "c20": False,
               "break_ma": False, "break_k_low": False, "detail": "略過"}
        result = _build_result(ticker, tech, fund, chip, ls, pos, df, exchange=exchange)
        raw = {"ticker": ticker, "exchange": exchange, "passed": False,
               "tech": tech, "fund": fund, "chip": chip, "ls": ls, "pos": pos}
        return raw, result
    else:
        return screen_one(sid)


def screen_batch(stock_ids: list[str], delay: float = 0, quick: bool = True,
                 max_workers: int = 2) -> tuple[list[dict], list[ScreeningResult]]:
    tdcc.load_from_disk_cache(cache, _CACHE_TTL)
    tickers = [_RESOLVED_TICKERS.get(sid) or f"{sid.strip().split('.TW')[0]}.TW" for sid in stock_ids]
    batch_prefetch_prices(tickers, cache, rate_limiter, retries=_REQ_RETRIES)

    print(f"\n==> 批次篩選 {len(stock_ids)} 檔 ({max_workers} 執行緒) ...\n")
    results_raw = []
    results_scored = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sid = {}
        for sid in stock_ids:
            future = executor.submit(_process_one_stock, sid, quick=quick)
            future_to_sid[future] = sid

        for future in tqdm(as_completed(future_to_sid), total=len(future_to_sid),
                           desc="篩選進度", unit="檔"):
            sid = future_to_sid[future]
            tqdm.write(f"  [{sid}] 完成")
            try:
                raw, result = future.result()
                results_raw.append(raw)
                results_scored.append(result)
            except Exception as e:
                ticker = sid.strip().split(".TW")[0]
                _, exchange = _resolve_ticker_suffix(ticker)
                tqdm.write(f"  [{ticker}] 錯誤: {e}")
                results_raw.append({"ticker": ticker, "exchange": exchange, "passed": False})
                results_scored.append(_build_result(ticker, {}, {}, {}, {}, {}, None, exchange=exchange))

    tdcc.save_to_disk_cache(cache)
    cache.flush()
    return results_raw, results_scored


def screen_batch_prioritized(stock_ids: list[str], quick: bool = True) -> tuple[list[dict], list[ScreeningResult]]:
    """P4-3: 優先順序篩選 (市值 > 500億 優先)，自動跳過已下市股"""
    print(f"\n[P4-3] 正在預取得市值資訊以進行優先排序...")
    _warm_yf_cache()

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    ticker_mc = []
    skipped = 0
    resolved = {}  # sid → yahoo_ticker

    for sid in stock_ids:
        ticker = sid.strip().split(".TW")[0]

        exchange = get_exchange(ticker)
        ticker_yf = f"{ticker}.{exchange}"

        info = fetch_info(ticker_yf, cache, rate_limiter, retries=_REQ_RETRIES)
        mc = info.get("marketCap", 0) if info else 0
        if not mc and exchange == "TW":
            # twstock 說上市但 yfinance 無市值 → 試 OTC
            info = fetch_info(f"{ticker}.TWO", cache, rate_limiter, retries=_REQ_RETRIES)
            mc = info.get("marketCap", 0) if info else 0
            if mc:
                ticker_yf = f"{ticker}.TWO"
                exchange = "TWO"
        if not mc:
            skipped += 1
            print(f"  ⏭️  {ticker}: 跳過（無股價資料，可能已下市）")
            continue

        resolved[sid] = ticker_yf
        ticker_mc.append((sid, mc))

    logging.getLogger("yfinance").setLevel(logging.WARNING)

    ticker_mc.sort(key=lambda x: x[1], reverse=True)
    sorted_ids = [x[0] for x in ticker_mc]

    global _RESOLVED_TICKERS
    _RESOLVED_TICKERS = resolved

    if skipped:
        print(f"  ⚠  已跳過 {skipped} 檔下市股")
    if resolved:
        two_count = sum(1 for v in resolved.values() if v.endswith(".TWO"))
        if two_count:
            print(f"  📌 {two_count} 檔 OTC 上櫃股 (使用 .TWO 後綴)")
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
    print(f"  篩選完成: {len(results_scored)} 檔")
    print(f"  {Fore.GREEN}🟢 ENTER={len(enters)}{Style.RESET_ALL}  {Fore.YELLOW}🟡 WATCH={len(watches)}{Style.RESET_ALL}  {Fore.RED}🔴 EXIT={len(exits)}{Style.RESET_ALL}  {Style.DIM}⚫ OUT={len(outs)}{Style.RESET_ALL}")
    print(f"{'='*65}")

    # ---- ENTER ----
    if enters:
        print(f"\n{Fore.GREEN}{'━'*62}")
        print(f"  🟢 ENTER — 適合進場 (得分≥75 + 核心條件全過)")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in sorted(enters, key=lambda x: -x.score):
            print(f"  {Fore.GREEN}{Style.BRIGHT}{r.ticker}{Style.RESET_ALL} | 得分 {r.score} | 收盤 {r.close} | MA20 {r.ma20} | MA60 {r.ma60}")
    else:
        print(f"\n  🟢 無 ENTER 級股票")

    # ---- WATCH ----
    if watches:
        print(f"\n{Fore.YELLOW}{'━'*62}")
        print(f"  🟡 WATCH — 追蹤價值 (得分40-74)")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in sorted(watches, key=lambda x: -x.score):
            missing = []
            if not r.c1: missing.append("C1")
            if not r.c14: missing.append("C14")
            if not r.c15: missing.append("C15")
            print(f"  {Fore.YELLOW}{r.ticker}{Style.RESET_ALL} | 得分 {r.score} | 收盤 {r.close} | 缺: {', '.join(missing) or '核心條件OK(分數不足)'}")
    else:
        print(f"\n  🟡 無 WATCH 級股票")

    # ---- EXIT ----
    if exits:
        print(f"\n{Fore.RED}{'━'*62}")
        print(f"  🔴 EXIT — 出場信號")
        print(f"{'━'*62}{Style.RESET_ALL}")
        for r in exits:
            print(f"  {Fore.RED}{r.ticker}{Style.RESET_ALL} | 得分 {r.score} | 出場: {r.exit_signals}")
    else:
        print(f"\n  🔴 無 EXIT 級股票")

    # ---- 詳細報告 (ENTER + WATCH) ----
    detailed = enters + watches
    if detailed:
        print(f"\n{'='*65}")
        print(f"  📋 詳細報告")
        print(f"{'='*65}")

    for r in detailed:
        # 找到對應的 raw 結果
        raw = next((rr for rr in results_raw if rr["ticker"] == r.ticker), None)
        if not raw:
            continue
        tt = raw["tech"]
        ft = raw["fund"]
        ct = raw["chip"]
        lt = raw["ls"]
        pt = raw["pos"]

        c16_c = CONFIG["conditions"]["C16"]

        print(f"\n{'─'*62}")
        tier_emoji = {TIER_ENTER: "🟢", TIER_WATCH: "🟡"}
        color = Fore.GREEN if r.tier == TIER_ENTER else Fore.YELLOW
        print(f"  {color}{tier_emoji.get(r.tier, '')} {r.ticker}{Style.RESET_ALL} | 得分 {r.score} | 收盤 {r.close} | MA20 {r.ma20} | MA60 {r.ma60} | 量比 {r.vol_ratio}")
        print(f"{'─'*62}")
        print(f"  {'條件':<22} {'結果':<6} {'得分'}")
        print(f"{'─'*62}")
        for key, weight in STOCK_SCORE_WEIGHTS.items():
            actual_key = key.replace("_bonus", "")
            val = getattr(r, actual_key, False)
            got = weight if val else 0
            label_map = {
                "c1": "C1 收>60MA且向上", "c2": "C2 收>20MA", "c3": "C3 前Low<20MA",
                "c4": "C4 量>5日均量", "c5": "C5 EPS>分組門檻", "c6": "C6 負債比<分組門檻",
                "c7": "C7 營收YoY>分組門檻", "c9": "C9 存貨天數<365",
                "c10": "C10 存貨正常區間", "c11": "C11 法人連買",
                "c12": "C12 大戶收", "c13": "C13 非散戶接",
                "c14": "C14 距60MA回測", "c15": "C15 連3日上漲",
                "c17": "C17 非高檔破月線",
                "c18": "C18 攻擊訊號",
                "c19": "C19 多頭買點",
            }
            label = label_map.get(actual_key, actual_key)
            print(f"  {label:<22} {_ok(val):<6} {got:>2}/{weight}")

        # 硬淘汰/出場
        if r.hard_rejected:
            print(f"  {'⛔ 硬淘汰':<22}")
        if r.exit_signals:
            print(f"  {'🔴 出場信號':<22} {r.exit_signals}")

        # 操作紀律告警
        if pt.get("break_ma"):
            print(f"  {'🛑 破均線':<22} 跌破剛站上的20MA")
        if pt.get("break_k_low"):
            print(f"  {'🛑 破K低':<22} {pt.get('break_k_low_detail','')}")
        print(f"{'─'*62}")


# ============================================================
#  主程式
# ============================================================

def _load_candidates(path: str = "candidates.csv") -> list[str]:
    import csv
    if not os.path.exists(path):
        print(f"  ⚠ 找不到 {path}，使用預設範例")
        return ["2330", "2317", "2454"]
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["ticker"].strip() for row in reader if row.get("ticker","").strip()]


if __name__ == "__main__":
    CANDIDATES = _load_candidates()

    print("=" * 65)
    print("  台股篩選 v4 — 技術+基本面+籌碼+位階+買賣點 (20條件)")
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
