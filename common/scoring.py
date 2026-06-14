"""得分制 + 三層輸出 (ENTER/WATCH/EXIT) + 結果持久化"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import csv
import os

import numpy as np
import pandas as pd


# ============================================================
#  權重表（個股版，總分 100）
#  ※ 正式執行時優先讀取 config.json/etf.json 的 scoring_weights
# ============================================================

STOCK_SCORE_WEIGHTS = {
    # 技術面 (30 分)
    "c1": 10,
    "c2": 5,
    "c3": 5,
    "c4": 10,
    # 基本面 (30 分)
    "c5": 5,
    "c6": 5,
    "c7": 10,
    "c9": 5,
    "c10": 5,
    # 籌碼面 (20 分)
    "c11": 10,
    "c12": 5,
    "c13": 5,
    # 位階+攻擊 (20 分)
    "c14": 5,
    "c15": 5,
    "c18": 10,
    # C19 加分（非必須）
    "c19_bonus": 5,
}

ETF_SCORE_WEIGHTS = {
    # 技術面 (30 分)
    "c1": 10,
    "c2": 5,
    "c3": 5,
    "c4": 10,
    # 基本面 (20 分) — ETF 基本面項目少
    "c5": 5,
    "c6": 5,
    "c7": 5,
    "c8": 5,
    # 籌碼面 (25 分) — ETF 看重籌碼
    "c11": 10,
    "c12": 10,
    "c13": 5,
    # 位階+攻擊 (25 分)
    "c14": 5,
    "c15": 5,
    "c18": 10,
    # C19 加分（非必須）
    "c19_bonus": 5,
}


# ============================================================
#  分類門檻預設值（可由 config 覆蓋）
# ============================================================

DEFAULT_TIER_THRESHOLDS = {"enter_min": 75, "watch_min": 40}
DEFAULT_HARD_REJECT_RULES = {"c16": False, "c17": False, "c20": True, "c13": False}
DEFAULT_EXIT_PARAMS = {
    "ma20_period": 20,
    "ma60_period": 60,
    "lookback_days_e1": 30,
    "lookback_days_e2": 40,
    "high_ratio_e2": 0.95,
    "volume_ratio_e4": 0.7,
    "volume_shrink_ratio_e5": 0.6,   # E5: 量縮門檻（成交量 < 5日均量 × 60%）
    "consecutive_shrink_e5": 3,       # E5: 連續量縮天數
}


# ============================================================
#  三層分類
# ============================================================

TIER_ENTER = "ENTER"    # 適合進場
TIER_WATCH = "WATCH"    # 追蹤價值
TIER_EXIT  = "EXIT"     # 出場信號
TIER_OUT   = "OUT"      # 不合格


def classify_tier(score: int, hard_rejected: bool,
                  c1: bool, c14: bool, c15: bool,
                  exit_signals: list = None,
                  thresholds: dict = None) -> str:
    if thresholds is None:
        thresholds = DEFAULT_TIER_THRESHOLDS
    if exit_signals:
        return TIER_EXIT
    if hard_rejected:
        return TIER_OUT
    if score >= thresholds["enter_min"] and c1 and c14 and c15:
        # 設計意圖：ENTER 需同時具備 c1(站穩60MA) + c14(距60MA 0~5%) + c15(短多攻擊)
        # 此組合偏向「突破/回測完成進場」，強趨勢股（遠離60MA）可能只達 WATCH
        return TIER_ENTER
    if score >= thresholds["watch_min"]:
        return TIER_WATCH
    return TIER_OUT


# ============================================================
#  得分計算
# ============================================================

def calc_score(conditions: dict, weights: dict) -> int:
    """計算加權得分

    Args:
        conditions: {條件名: bool} 如 {"c1": True, "c2": False, ...}
        weights: 權重表

    Returns:
        整數得分 (0~100)
    """
    total = 0
    for key, weight in weights.items():
        if key.endswith("_bonus"):
            # 加分項：通過才加，不通過不扣
            if conditions.get(key.replace("_bonus", ""), False):
                total += weight
        else:
            if conditions.get(key, False):
                total += weight
    return min(total, 100)


def check_hard_reject(conditions: dict, rules: dict = None) -> bool:
    if rules is None:
        rules = DEFAULT_HARD_REJECT_RULES
    for key, expected in rules.items():
        if conditions.get(key) == expected:
            return True
    return False


# ============================================================
#  出場信號（Stateless，不需持有紀錄）
# ============================================================

def check_exit(df, pos: dict, params: dict = None) -> list[str]:
    if df is None or len(df) < 60:
        return []
    if params is None:
        params = DEFAULT_EXIT_PARAMS

    close = df["close"].to_numpy()
    volume = df["volume"].to_numpy()
    ma20 = pd.Series(close).rolling(params["ma20_period"]).mean().to_numpy()
    ma60 = pd.Series(close).rolling(params["ma60_period"]).mean().to_numpy()
    reasons = []

    # E1: 前 N 天內曾站穩月線 → 現在跌破月線
    e1_lookback = params["lookback_days_e1"]
    if len(close) >= e1_lookback:
        was_above = False
        for i in range(max(0, len(close) - e1_lookback), len(close)):
            if not np.isnan(ma20[i]) and close[i] > ma20[i]:
                was_above = True
                break
        if was_above and not np.isnan(ma20[-1]) and close[-1] < ma20[-1]:
            reasons.append("E1: 多頭破月線(前30日曾站上)")

    # E2: 仍處高檔 + KD 死叉（前半段高點 vs 近期高點）
    e2_lookback = params["lookback_days_e2"]
    if len(close) >= e2_lookback:
        prior_high = np.max(close[max(0, len(close)-e2_lookback):max(0, len(close)-e2_lookback//2)])
        recent_high = np.max(close[max(0, len(close)-e2_lookback//2):-1])
        kd_death = pos.get("c20", False)
        if recent_high >= prior_high * params["high_ratio_e2"] and kd_death:
            reasons.append("E2: 高位死叉(仍近前高+KD死叉)")

    # E3: 跌破60MA + 60MA 走平/向下
    if not np.isnan(ma60[-1]) and not np.isnan(ma60[-5]):
        if close[-1] < ma60[-1] and ma60[-1] <= ma60[-5]:
            reasons.append("E3: 跌破季線+季線走平/向下")

    # E4: 連跌4日 + 量能急縮
    if len(close) >= 5:
        consec_down = all(close[-i] <= close[-i-1] for i in range(1, 5))
        vol_ma5 = pd.Series(volume).rolling(5).mean().to_numpy()
        vol_shrink = not np.isnan(vol_ma5[-1]) and volume[-1] < vol_ma5[-1] * params["volume_ratio_e4"]
        if consec_down and vol_shrink:
            reasons.append("E4: 連跌4日+量縮<5MA70%")

    # E5: 連續量縮 + 價穩（主力退場前兆）
    # 連續 N 日成交量 < 5日均量 × shrink_ratio，且股價仍在 MA20 之上
    shrink_ratio = params.get("volume_shrink_ratio_e5", 0.6)
    consec_shrink = params.get("consecutive_shrink_e5", 3)
    if len(close) >= consec_shrink + 5:
        vol_ma5 = pd.Series(volume).rolling(5).mean().to_numpy()
        shrink_days = 0
        for i in range(1, consec_shrink + 1):
            idx = -i
            if (not np.isnan(vol_ma5[idx]) and
                    volume[idx] < vol_ma5[idx] * shrink_ratio):
                shrink_days += 1
            else:
                break
        # 價穩：近 N 日收盤都在 MA20 之上
        above_ma20 = (not np.isnan(ma20[-1]) and close[-1] > ma20[-1])
        if shrink_days >= consec_shrink and above_ma20:
            reasons.append(f"E5: 連{consec_shrink}日量縮<{shrink_ratio*100:.0f}%5MA+價穩在月線上")

    return reasons


# ============================================================
#  結果資料結構 + 持久化
# ============================================================

@dataclass
class ScreeningResult:
    ticker: str = ""
    type_label: str = ""       # ETF 用
    timestamp: str = ""
    tier: str = TIER_OUT       # ENTER / WATCH / EXIT / OUT
    score: int = 0
    hard_rejected: bool = False
    # 技術面
    c1: bool = False
    c2: bool = False
    c3: bool = False
    c4: bool = False
    # 基本面
    c5: bool = False
    c6: bool = False
    c7: bool = False
    c8: bool = False       # ETF 專用 / 個股已移除
    c9: bool = False
    c10: bool = False
    # 籌碼面
    c11: bool = False
    c12: bool = False
    c13: bool = False      # True = 非散戶接（安全）
    # 位階
    c14: bool = False
    c15: bool = False
    c16: bool = True       # True = 未翻倍（安全）
    c17: bool = True       # True = 非高檔震盪（安全）
    # 買賣點
    c18: bool = False
    c19: bool = False
    c20: bool = False      # True = 空頭賣點觸發
    # 附加
    exit_signals: str = ""     # 逗號分隔
    close: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    vol_ratio: float = 0.0
    detail_score: str = ""     # 各項得分明細
    exchange: str = ""         # TW / TWO


def save_results(results: list[ScreeningResult], base_dir: str = "") -> str:
    """將結果追加寫入 CSV，按月分檔

    Returns:
        寫入的檔案路徑
    """
    if not base_dir:
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screening_results")
    Path(base_dir).mkdir(exist_ok=True)

    today = datetime.now().strftime("%Y%m")
    filename = f"screening_{today}.csv"
    filepath = os.path.join(base_dir, filename)

    is_new = not os.path.exists(filepath)
    with open(filepath, "a", newline="", encoding="utf-8-sig") as f:
        fieldnames = list(ScreeningResult.__dataclass_fields__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            writer.writeheader()
        for r in results:
            row = asdict(r)
            row["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            writer.writerow(row)

    return filepath
