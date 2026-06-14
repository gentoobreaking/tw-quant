"""KD 指標計算（含 warmup 標記）"""
import numpy as np


# KD 需要足夠的 K 棒數量才能收斂到真實值
# 經驗法則：warmup = period * 2（KD(20,5,5) 需 ~40 根，KD(9,3,3) 需 ~18 根）
# 前 warmup 根 K/D 仍受初始值 50 影響，標記為 NaN 避免誤用


def calc_kd(high: np.ndarray, low: np.ndarray, close: np.ndarray,
            period: int = 9, k_smooth: int = 3, d_smooth: int = 3,
            warmup: bool = True
            ) -> tuple[np.ndarray, np.ndarray]:
    """計算 KD 指標

    Args:
        high: 最高價陣列
        low: 最低價陣列
        close: 收盤價陣列
        period: RSV 計算期間 (預設 9)
        k_smooth: K 值平滑期 (預設 3, 即 1/3 權重)
        d_smooth: D 值平滑期 (預設 3, 即 1/3 權重)
        warmup: 是否將前 N 根受初始值影響的 K/D 標記為 NaN (預設 True)

    Returns:
        (K, D) 陣列

    Note:
        個股用 (9,3,3)，ETF 用 (20,5,5) — 在 config 中設定
        warmup 區間 = period * 2，此區間內 K/D 受初始值 50 影響不具參考意義
    """
    n = len(close)
    k = np.full(n, 50.0)
    d = np.full(n, 50.0)
    k_weight = 1.0 / k_smooth
    d_weight = 1.0 / d_smooth

    for i in range(period - 1, n):
        h = np.max(high[i - period + 1: i + 1])
        l = np.min(low[i - period + 1: i + 1])
        rsv = (close[i] - l) / (h - l) * 100 if h != l else 50
        if i == period - 1:
            k[i] = rsv
        else:
            k[i] = (1 - k_weight) * k[i - 1] + k_weight * rsv
        d[i] = (1 - d_weight) * d[i - 1] + d_weight * k[i]

    # warmup 區間標記為 NaN
    if warmup:
        warmup_len = min(period * 2, n)
        k[:warmup_len] = np.nan
        d[:warmup_len] = np.nan

    return k, d
