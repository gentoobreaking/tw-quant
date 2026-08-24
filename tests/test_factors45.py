"""T010 單元測試 — 因子④⑤（合成 K 線序列）"""
import numpy as np
import pandas as pd
import pytest

from common.factors import score_momentum, score_position


def synth_history(prices, volumes=None):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="B")
    vols = volumes if volumes is not None else np.full(len(prices), 1_000_000)
    return pd.DataFrame({"Close": prices, "Volume": vols}, index=idx)


def uptrend(n=120, start=100, slope=0.5):
    return np.array([start + slope * i for i in range(n)])


def test_momentum_uptrend_all_pass():
    prices = uptrend(120)
    # 量能：後段放大 → 5日均量 > 20日均量
    vols = np.linspace(1e6, 3e6, 120)
    r = score_momentum("X", history_fn=lambda t: synth_history(prices, vols),
                       index_history_fn=lambda: synth_history(
                           [100] * 120))   # 大盤平盤
    assert r["f4"] == 15
    assert all(r["_sub"].values())
    assert r["ma20"] > r["ma60"]
    assert r["dist_60d_high"] == pytest.approx(0.0)   # 收在最高
    assert r["rsi14"] > 70


def test_momentum_downtrend_mostly_fail():
    prices = list(np.linspace(200, 100, 120))         # 下跌趨勢
    vols = np.linspace(3e6, 1e6, 120)
    r = score_momentum("Y", history_fn=lambda t: synth_history(prices, vols),
                       index_history_fn=lambda: synth_history([100] * 120))
    assert r["f4"] <= 6
    assert not r["_sub"]["above_ma20"]
    assert not r["_sub"]["ma20_up"]


def test_position_drawdown_bands():
    # 60 日高 150 → 現價 135：回撤 10%（≥5% ✓ <8% ✗ for 120日）
    prices = np.concatenate([np.full(60, 150.0), np.linspace(150, 135, 60)])
    r = score_position("A", history_fn=lambda t: synth_history(prices))
    assert r["_sub"]["dd60"] is True
    assert r["_sub"]["dd120"] is True   # 10% ≥ 8%


def test_position_lower_half_52w():
    prices = np.concatenate([np.linspace(200, 100, 130), np.full(10, 105.0)])
    r = score_position("B", history_fn=lambda t: synth_history(prices))
    assert r["_sub"]["lower_half_52w"] is True


def test_golden_cross_recent():
    # 前 90 日下跌、後 30 日急漲 → 20MA 在近 5 日內上穿 60MA
    down = np.linspace(200, 100, 95)
    up = np.linspace(100, 190, 30)
    prices = np.concatenate([down, up])
    r = score_momentum("C", history_fn=lambda t: synth_history(prices),
                       index_history_fn=lambda: synth_history([100] * len(prices)))
    assert r["_sub"]["golden"], "應偵測到黃金交叉"


def test_insufficient_history():
    r = score_momentum("D", history_fn=lambda t: synth_history([100, 101]))
    assert r["f4"] == 0 and r["note"] == "歷史資料不足"
