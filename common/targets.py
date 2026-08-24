"""進場區／停損／目標價／風報比（T011 使用、T013 消費）

公式出處：algs/entry-stop-target.md。純確定性計算，全體股票都算。
"""
from __future__ import annotations

from typing import Optional


def compute_targets(mom: dict, eps_result: dict,
                    rr_thresholds: Optional[dict] = None) -> dict:
    """由 score_momentum／score_eps_revision 的結果計算買點欄位

    mom 需含：close, ma20, ma60, high_60d, launch_low
    eps_result 需含：target_mean
    """
    rr_thresholds = rr_thresholds or {"s": 2.0, "a": 1.5}
    out = {
        "entry_low": None, "entry_high": None,
        "stop_loss": None, "target_price": None,
        "rr": None, "target_note": "",
    }
    close = mom.get("close")
    ma20, ma60 = mom.get("ma20"), mom.get("ma60")
    if None in (close, ma20, ma60):
        return {**out, "note": "資料不足"}

    # 前置資格：現價 > 60MA 且現價 > 20MA 且 20MA 向上
    if not (close > ma60 and close > ma20 and mom.get("ma20_up")):
        return {**out, "note": "架構轉弱，不進場"}
    # 多頭排列檢查
    if not (ma20 > ma60):
        return {**out, "note": "待 20MA 站上 60MA 後再評"}

    entry_low = round(ma20, 2)
    entry_high = round(ma20 * 1.03, 2)
    mid = (entry_low + entry_high) / 2

    # 技術停損
    launch_low = mom.get("launch_low")
    stop_candidates = [mid * 0.93]
    if launch_low:
        stop_candidates.append(launch_low * 0.995)
    stop_loss = round(min(stop_candidates), 2)

    # 目標價：近 60 日高 vs 分析師 mean，取離現價較近者；空間 <8% 改用較遠者
    target_mean = eps_result.get("target_mean")
    candidates = {"high_60d": mom.get("high_60d"), "analyst_mean": target_mean}
    candidates = {k: v for k, v in candidates.items() if v}
    if candidates:
        nearest_key = min(candidates, key=lambda k: abs(candidates[k] - close))
        target_price = candidates[nearest_key]
        upside = (target_price / close - 1) * 100
        if upside < 8 and len(candidates) > 1:
            farther_key = max(candidates, key=lambda k: abs(candidates[k] - close))
            target_price = candidates[farther_key]
            out["target_note"] = f"近目標空間不足（{upside:.1f}%），改用較遠者"
        out["target_source"] = nearest_key if target_price == candidates[nearest_key] \
            else ("analyst_mean" if target_mean == target_price else "high_60d")

    rr = None
    if target_price and stop_loss < mid:
        rr = round((target_price - mid) / (mid - stop_loss), 2)

    out.update({
        "entry_low": entry_low, "entry_high": entry_high,
        "stop_loss": stop_loss, "target_price": round(target_price, 2)
        if target_price else None,
        "rr": rr,
    })
    return out
