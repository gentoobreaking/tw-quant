"""T008 單元測試 — 因子②計分（fixture：2026-08-25 台積電 Yahoo 實抓值）"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from common.factors import score_eps_revision


def make_ee(rows):
    df = pd.DataFrame(rows).T
    df.index.name = "period"
    return df


def make_provider(ee=None, trend=None, revisions=None, growth=None, targets=None):
    return lambda t: {
        "earnings_estimate": ee,
        "eps_trend": trend,
        "eps_revisions": revisions,
        "growth_estimates": growth,
        "analyst_price_targets": targets or {},
    }


# fixture：2026-08-25 實抓（2330.TW）
EE = make_ee({
    "0y":  {"avg": 107.6434, "numberOfAnalysts": 34, "growth": 0.6248},
    "+1y": {"avg": 142.12909, "numberOfAnalysts": 34, "growth": 0.3204},
})
TREND = make_ee({
    "0y": {"current": 107.6434, "30daysAgo": 106.65105, "90daysAgo": 97.99868},
})
REVISIONS = make_ee({
    "0y": {"upLast30days": 28, "downLast30days": 0},
})
GROWTH = make_ee({
    "0y": {"stockTrend": 0.6248, "indexTrend": 0.3117},
})
TARGETS = {"mean": 3229.3333}


def test_tsmc_fixture_full_score():
    r = score_eps_revision("2330.TW", provider=make_provider(EE, TREND, REVISIONS, GROWTH, TARGETS))
    # rev_1m = (107.6434−106.65105)/106.65105 ≈ +0.93% → 6 分（0~2% 帶）
    assert r["rev_1m"] == pytest.approx(0.93, abs=0.01)
    assert r["_sub"]["rev_1m"] == 6
    # rev_3m = vs 97.99868 ≈ +9.84% → 6 分（5~10% 帶）
    assert r["rev_3m"] == pytest.approx(9.84, abs=0.05)
    assert r["_sub"]["rev_3m"] == 6
    # up28/down0 → 6 分
    assert r["_sub"]["revisions"] == 6
    # stock 62.5% > industry 31.2% → 4 分
    assert r["_sub"]["industry_rel"] == 4
    assert r["f2"] == 22
    assert r["eps_2026"] == pytest.approx(107.6434)
    assert r["eps_2027"] == pytest.approx(142.12909)
    assert r["target_mean"] == pytest.approx(3229.3333)
    assert r["analysts"] == 34


def test_no_coverage_when_few_analysts():
    ee = make_ee({"0y": {"avg": 5.0, "numberOfAnalysts": 2}})
    r = score_eps_revision("X.TW", provider=make_provider(ee))
    assert r["f2"] == 0 and r["note"].startswith("無覆蓋")
    assert r["eps_2026"] is None


def test_downgrade_all_zero():
    trend = make_ee({
        "0y": {"current": 90.0, "30daysAgo": 100.0, "90daysAgo": 100.0},
    })
    rev = make_ee({"0y": {"upLast30days": 1, "downLast30days": 9}})
    growth = make_ee({"0y": {"stockTrend": 0.1, "indexTrend": 0.3}})
    r = score_eps_revision("Y.TW", provider=make_provider(EE, trend, rev, growth))
    assert r["f2"] == 0          # 全子項歸零
    assert r["rev_1m"] == -10.0


def test_boundary_bands():
    rev_eq = make_ee({"0y": {"upLast30days": 3, "downLast30days": 3}})
    base_ee = make_ee({"0y": {"avg": 10.0, "numberOfAnalysts": 5}})

    # 恰好 5% → 12 分帶；恰好 10%（3m）→ 8 分帶
    r = score_eps_revision("Z.TW", provider=make_provider(
        base_ee,
        make_ee({"0y": {"current": 105.0, "30daysAgo": 100.0, "90daysAgo": 95.4545}}),
        rev_eq,
        make_ee({"0y": {"stockTrend": 0.4, "indexTrend": 0.4}})))
    assert r["_sub"]["rev_1m"] == 12       # +5.00%
    assert r["_sub"]["rev_3m"] == 8        # +10.00%
    assert r["_sub"]["revisions"] == 2     # up==down → 2 分
    assert r["_sub"]["industry_rel"] == 0  # 相等不給分


# ---- T014 增補：成功才快取、失敗下次回補 ----
def test_cache_backfill_incomplete_not_cached():
    calls = {"n": 0}
    cache = MagicMock()
    store = {}
    cache.get.side_effect = lambda key, fn, ttl=None, skip_none=False: (
        store.get(key))

    def fake_save(key, entry):
        pass
    # 模擬 DiskCache：skip_none=True 時 None 不入快取
    def get_impl(key, fn, ttl=None, skip_none=False):
        val = store.get(key)
        if val is not None:
            return val
        val = fn()
        if not (val is None and skip_none):
            store[key] = val
        return val
    cache.get.side_effect = get_impl

    def provider(t):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一次：抓取失敗（無覆蓋）→ _complete=False
            return {"earnings_estimate": make_ee({"0y": {"avg": 5.0,
                    "numberOfAnalysts": 1}})}
        # 第二次：資料回來了
        return make_provider(EE, TREND, REVISIONS, GROWTH, TARGETS)(t)

    r1 = score_eps_revision("2330.TW", cache=cache, provider=provider)
    assert r1["_complete"] is False and r1["f2"] == 0

    r2 = score_eps_revision("2330.TW", cache=cache, provider=provider)
    assert r2["_complete"] is True          # 第二次執行有回補
    assert r2["f2"] > 0


def test_cache_complete_is_cached():
    calls = {"n": 0}
    store = {}
    cache = MagicMock()
    def get_impl(key, fn, ttl=None, skip_none=False):
        val = store.get(key)
        if val is not None:
            return val
        val = fn()
        if not (val is None and skip_none):
            store[key] = val
        return val
    cache.get.side_effect = get_impl

    def provider(t):
        calls["n"] += 1
        return make_provider(EE, TREND, REVISIONS, GROWTH, TARGETS)(t)

    score_eps_revision("2330.TW", cache=cache, provider=provider)
    score_eps_revision("2330.TW", cache=cache, provider=provider)   # 走快取
    assert calls["n"] == 1               # 完整資料只抓一次
