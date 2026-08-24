"""硬淘汰規則引擎（T012）與 S/A/B 訊號分級（T013）

規則出處：algs/signal-grading.md
"""
from __future__ import annotations

import pandas as pd

from .logger import logger


def apply_hard_rejects(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """套用 H1–H5；回傳 (通過 DataFrame, 淘汰/降分 DataFrame)

    rejected_rules 欄記錄觸發編號（H5 為降分，仍保留於 passed）。
    """
    if df.empty:
        return df.copy(), pd.DataFrame()

    rules = []
    out = df.copy()
    out["rejected_rules"] = ""

    def has(col):
        return col in out.columns and out[col].notna()

    # H1：2027 EPS 預估負成長
    h1 = pd.Series(False, index=out.index)
    covered1 = pd.Series(False, index=out.index)
    if "growth_2027" in out.columns:
        covered1 = out["growth_2027"].notna()
        h1 = covered1 & (out["growth_2027"] < 0)
    out.loc[~covered1, "rejected_rules"] += _join(out, "H1未檢(無覆蓋)")
    rules.append(("H1", h1))

    # H2：近 3 個月 EPS 大幅下修（rev_3m < −5%）
    h2 = pd.Series(False, index=out.index)
    covered2 = pd.Series(False, index=out.index)
    if "rev_3m" in out.columns:
        covered2 = out["rev_3m"].notna()
        h2 = covered2 & (out["rev_3m"] < -5)
    out.loc[~covered2, "rejected_rules"] += _join(out, "H2未檢(無覆蓋)")
    rules.append(("H2", h2))

    # H3：法人持續大幅賣超（外資20日 與 投信20日 同時淨賣超）
    h3 = pd.Series(False, index=out.index)
    if "foreign_20d" in out.columns and "trust_20d" in out.columns:
        cov = out["foreign_20d"].notna() & out["trust_20d"].notna()
        h3 = cov & (out["foreign_20d"] < 0) & (out["trust_20d"] < 0)
    rules.append(("H3", h3))

    # H4：基本面無成長只靠題材（EPS 成長 ≤0 且營收 YoY ≤0）
    h4 = pd.Series(False, index=out.index)
    if "growth_2026" in out.columns and "rev_yoy_3m" in out.columns:
        cov4 = out["growth_2026"].notna() & out["rev_yoy_3m"].notna()
        h4 = cov4 & (out["growth_2026"] <= 0) & (out["rev_yoy_3m"] <= 0)
    rules.append(("H4", h4))

    # 彙總淘汰
    rejected_mask = pd.Series(False, index=out.index)
    for name, mask in rules:
        rejected_mask |= mask.fillna(False)
    for name, mask in rules:
        hit = mask.fillna(False)
        out.loc[hit, "rejected_rules"] += _join(out[hit], name)

    rejected = out[rejected_mask].copy()
    passed = out[~rejected_mask].copy()

    # H5：前高極近且 RSI 過熱 → 降分（不淘汰）
    if not passed.empty and {"dist_60d_high", "rsi14"} <= set(passed.columns):
        h5 = ((passed["dist_60d_high"].abs() <= 3)
              & passed["rsi14"].notna() & (passed["rsi14"] > 70))
        passed.loc[h5, "total"] -= 10
        passed.loc[h5, "rejected_rules"] += _join(passed[h5], "H5降分")

    logger.info("硬淘汰：%d 檔通過 / %d 檔淘汰", len(passed), len(rejected))
    return passed, rejected


def _join(df, text):
    """在既有字串後附加規則標記（分號分隔）"""
    return df["rejected_rules"].apply(
        lambda s: f"{s};{text}" if s else text)


def grade_signals(passed: pd.DataFrame, rr_thresholds: dict) -> pd.DataFrame:
    """S/A/B 分級（T013）：由上而下首個符合即停止

    S：R/R ≥ s門檻 且 f2≥24 且 f3≥14 且 growth_2027>0
    A：f2≥18 且 growth_2027>0 且 R/R≥a門檻 且 f3<14（法人未轉）
    B：其餘 total ≥60（含 H5 降分後）
    未達 B → 標「C」僅列觀察
    """
    out = passed.copy()
    grades = []
    s_th = rr_thresholds.get("s", 2.0)
    a_th = rr_thresholds.get("a", 1.5)
    for _, row in out.iterrows():
        rr = row.get("rr")
        f2, f3 = row.get("f2", 0), row.get("f3", 0)
        g27 = row.get("growth_2027")
        total = row.get("total", 0)
        entry_ok = str(row.get("note", "")) == "" or "架構轉弱" not in \
            str(row.get("target_note", "") or "")
        g27_ok = g27 is not None and g27 > 0
        grade = None
        if rr is not None and rr >= s_th and f2 >= 24 and f3 >= 14 and g27_ok:
            grade = "S"
        elif f2 >= 18 and g27_ok and rr is not None and rr >= a_th and f3 < 14:
            grade = "A"
        elif total >= 60:
            grade = "B"
        grades.append(grade or "C")
    out["grade"] = grades
    return out
