"""T011 單元測試 — 加總器、兩份量化表、tie-break"""
import pandas as pd
import pytest

import common.scorer as scorer_mod
from common.scorer import run_scoring


def make_universe(n, counts=None):
    return pd.DataFrame({
        "ticker": [f"{2000+i}" for i in range(n)],
        "name": [f"股{i}" for i in range(n)],
        "sector": ["電子工業"] * n,
        "count": counts or [1] * n,
        "etf_sources": ["0050.TW"] * n,
    })


class FactorStub:
    """依 ticker 對照表回傳固定因子結果"""

    def __init__(self, per_ticker):
        self.per_ticker = per_ticker

    def eps(self, ticker, cache=None, **kw):
        s = self.per_ticker[ticker]
        return {"f2": s["f2"], "eps_2026": 10.0, "eps_2027": 12.0,
                "growth_2026": 0.3, "growth_2027": 0.2,
                "rev_1m": 3.0, "rev_3m": 6.0, "target_mean": s.get("tm", 120.0),
                "analysts": 10, "note": "", "_sub": {}}

    def fund(self, ticker, **kw):
        s = self.per_ticker[ticker]
        return {"f1": s["f1"], "_sub": {}, "eps_growth_2026": 0.3,
                "rev_yoy_3m": None, "roe": None, "gross_margin_q": None,
                "gross_margin_delta": None}

    def chips(self, ticker, **kw):
        s = self.per_ticker[ticker]
        return {"f3": s["f3"], "_sub": {},
                "foreign_20d": 100.0, "trust_20d": 50.0}

    def mom(self, ticker):
        s = self.per_ticker[ticker]
        return {"f4": s["f4"], "_sub": {}, "ma20": 98.0, "ma60": 90.0,
                "dist_60d_high": -2.0, "rsi14": 55, "close": 100.0,
                "high_60d": s.get("h60", 130.0), "launch_low": 95.0,
                "ma20_up": True}

    def pos(self, ticker):
        return {"f5": s_f5(self.per_ticker[ticker]), "_sub": {}}


def s_f5(s):
    return s["f5"]


@pytest.fixture
def stub_factory(monkeypatch):
    def install(per_ticker):
        stub = FactorStub(per_ticker)
        monkeypatch.setattr(scorer_mod, "score_eps_revision", stub.eps)
        monkeypatch.setattr(scorer_mod, "score_fundamentals", stub.fund)
        monkeypatch.setattr(scorer_mod, "score_chips", stub.chips)
        monkeypatch.setattr(scorer_mod, "score_momentum",
                            lambda t, cache=None, **kw: stub.mom(t))
        monkeypatch.setattr(scorer_mod, "score_position",
                            lambda t, cache=None, **kw: stub.pos(t))
        return stub
    return install


CFG = {"rr_thresholds": {"s": 2.0, "a": 1.5}}


def test_scoring_totals_and_two_tables(stub_factory):
    per = {f"200{i}": {"f1": 15, "f2": 12, "f3": 8, "f4": 9, "f5": 5}
           for i in range(5)}
    per["2000"]["f2"] = 20                      # 拉高分數
    stub_factory(per)
    full, top10, details = run_scoring(make_universe(5), CFG, cache=None)

    assert len(full) == 5 and len(top10) == 5   # <10 時全保留
    assert full["total"].is_monotonic_decreasing
    assert set(top10["ticker"]).issubset(set(full["ticker"]))
    assert full.iloc[0]["ticker"] == "2000"
    assert full.iloc[0]["total"] == 15 + 20 + 8 + 9 + 5
    # 全量都有進場區/停損/目標價
    assert full["entry_low"].notna().all()
    assert full["stop_loss"].notna().all()
    assert full["rr"].notna().all()
    # 明細含子項
    assert len(details) == 5


def test_total_cap_at_100(stub_factory):
    stub_factory({f"200{i}": {"f1": 25, "f2": 30, "f3": 20, "f4": 15, "f5": 10}
                  for i in range(3)})
    full, _, _ = run_scoring(make_universe(3), CFG, cache=None)
    assert (full["total"] <= 100).all() and full.iloc[0]["total"] == 100


def test_top10_is_subset_of_full(stub_factory):
    n = 15
    per = {f"{2000+i}": {"f1": 5 + i % 5, "f2": 5 + i % 7, "f3": 5,
                         "f4": 6, "f5": 4}
           for i in range(n)}
    stub_factory(per)
    full, top10, _ = run_scoring(make_universe(n), CFG, cache=None)
    assert len(full) == n and len(top10) == 10
    assert set(top10["ticker"]).issubset(set(full["ticker"]))
    # 表二是表一子集且順序一致
    assert list(top10["ticker"]) == list(full["ticker"])[:10]


def test_tiebreak_by_f2_then_count(stub_factory):
    per = {
        "2000": {"f1": 15, "f2": 15, "f3": 8, "f4": 9, "f5": 5},
        "2001": {"f1": 15, "f2": 18, "f3": 8, "f4": 9, "f5": 5},
    }
    stub_factory(per)
    universe = make_universe(2, counts=[1, 3])   # count 不同但 total 同
    full, _, _ = run_scoring(universe, CFG, cache=None)
    assert list(full.ticker) == ["2001", "2000"]


def test_single_stock_failure_not_fatal(stub_factory, monkeypatch):
    per = {f"200{i}": {"f1": 15, "f2": 12, "f3": 8, "f4": 9, "f5": 5}
           for i in range(3)}
    stub = stub_factory(per)

    orig = scorer_mod.score_momentum
    def boom(t, cache=None, **kw):
        if t == "2001":
            raise RuntimeError("network")
        return orig(t, cache=cache, **kw) if False else stub.mom(t)
    monkeypatch.setattr(scorer_mod, "score_momentum", boom)

    full, top10, details = run_scoring(make_universe(3), CFG, cache=None)
    assert len(full) == 2 and "2001" not in full.ticker.values
