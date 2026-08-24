"""T009 單元測試 — 因子①③（mock 三路徑：primary OK／fallback／全失敗）"""
from unittest.mock import MagicMock, patch

import pytest

from common.factors import (_gross_margin_trend, _rev_yoy_finmind,
                            score_chips, score_fundamentals)


# ---- 因子① ----
def test_fundamentals_primary_ok():
    r = score_fundamentals(
        "2330", rate_limiter=None, finmind=None,
        eps_data={"growth_2026": 0.6248},
        roe_provider=lambda t: 0.28)
    # EPS growth 62.5% → 10；營收無資料 → 0；ROE 28% → 3；毛利率/FCF 無 → 0
    assert r["f1"] == 13
    assert r["_sub"]["eps_growth"] == 10
    assert r["_sub"]["roe"] == 3


def test_fundamentals_full_marks():
    r = score_fundamentals(
        "X", rate_limiter=None, finmind=MagicMock(),
        eps_data={"growth_2026": 0.40},
        roe_provider=lambda t: 0.20,
        fs_provider=lambda t: [
            {"date": "2026Q2", "type": "Revenue", "value": "100"},
            {"date": "2026Q2", "type": "GrossProfit", "value": "40"},   # 40%
            {"date": "2026Q1", "type": "Revenue", "value": "100"},
            {"date": "2026Q1", "type": "GrossProfit", "value": "35"},   # 35% ↑
        ],
        cf_provider=lambda t: [
            {"type": "營業活動之淨現金流入（出）", "value": "500"},
        ])
    assert r["_sub"]["gm_trend"] == 2     # 毛利率上升
    assert r["gross_margin_q"] == 40.0
    assert r["gross_margin_delta"] == 5.0
    assert r["_sub"]["fcf"] == 4
    assert r["f1"] == 10 + 0 + 3 + 2 + 4


def test_fundamentals_all_fail():
    r = score_fundamentals("Y", rate_limiter=None, finmind=None,
                           eps_data={"growth_2026": None})
    assert r["f1"] == 0


def test_rev_yoy_finmind_computation():
    rows = [{"date": f"{y}-{m:02d}-01", "revenue": v}
            for y, m, v in [(2025, 8, 100), (2025, 9, 100), (2026, 7, 150),
                            (2026, 8, 130), (2026, 9, 110)]]
    fm = MagicMock()
    fm.fetch_dataset.return_value = rows
    yoy = _rev_yoy_finmind(fm, "2330")
    # 2026-08 vs 2025-08 = +30%；2026-09 vs 2025-09 = +10%；7月缺去年 → 排除
    assert yoy == pytest.approx(20.0)


def test_gross_margin_single_quarter():
    gm, delta = _gross_margin_trend([
        {"date": "2026Q2", "type": "Revenue", "value": "200"},
        {"date": "2026Q2", "type": "GrossProfit", "value": "60"},
    ])
    assert gm == 30.0 and delta is None


# ---- 因子③ ----
def test_chips_twse_primary():
    def fake_window(ticker, days, cache, rl, max_back=45):
        row = ["2330", "台積電",
               "100", "50", "50",       # 外陸資買/賣/超
               "0", "0", "0",           # 外資自營
               "30", "10", "20",        # 投信買/賣/超
               "0", "70"]               # 自營商合計、三大法人
        return {f"2026082{i}": [row] for i in range(days)}
    cache = MagicMock()
    cache.get.side_effect = lambda key, fn, **kw: fn()   # 直接執行 compute
    with patch("common.factors._t86_window", side_effect=fake_window) as mp:
        r = score_chips("2330", cache=cache, rate_limiter=MagicMock(),
                        finmind=MagicMock())
    assert mp.call_count == 2   # 20日窗＋5日窗
    # 外資5日+4000張→6分；外資20日+20000張→4分；投信5日→4分；投信20日→2分；
    # 同向買超→2分；外資持股無 FinMind 資料→跳過
    assert r["f3"] == 18


def test_chips_finmind_fallback():
    fm = MagicMock()
    rows = []
    for d in range(20):
        rows += [
            {"date": f"2026-08-{d+1:02d}", "investor": "ForeignInvestor",
             "buy": 1000, "sell": 500},      # 每日淨買 +500 張
            {"date": f"2026-08-{d+1:02d}", "investor": "InvestmentTrust",
             "buy": 200, "sell": 300},       # 每日淨賣 −100 張
        ]
    fm.fetch_dataset.side_effect = lambda ds, tid=None, **kw: (
        rows if ds == "TaiwanStockInstitutionalInvestorsBuySell" else [])
    r = score_chips("2330", cache=None, rate_limiter=None, finmind=fm)
    assert r["_sub"]["foreign_20d"] == 4     # 正 → 4 分
    assert r["_sub"]["trust_20d"] == 0       # 負 → 0 分
