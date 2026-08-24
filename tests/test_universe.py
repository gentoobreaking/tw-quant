"""T006 單元測試 — 代號正規化、去重計數、延伸補足、ETF 排名"""
import pandas as pd
import pytest

from common.universe import build_pool, normalize_ticker, rank_etfs


# ---- normalize_ticker ----
@pytest.mark.parametrize("raw,expected", [
    ("2330.TW", "2330"),
    ("3260O", "3260"),          # Yahoo 上櫃標記
    ("0050B.TW", None),         # ETF 分割股碼
    ("0050.TW", "0050"),        # 本身是 4 位數字（由 exclude 負責剔除）
    ("ABC", None),
    ("1101B", None),            # 5 位非 O 尾
])
def test_normalize(raw, expected):
    assert normalize_ticker(raw) == expected


TOP5 = [{"ticker": "0050.TW", "name": "A", "category": "市值型"},
        {"ticker": "00878.TW", "name": "B", "category": "高股息"}]


def test_build_pool_dedup_and_count():
    holdings = {
        "0050.TW": ["2330.TW", "2882.TW"],
        "00878.TW": ["2330.TW", "3260O"],   # 3260O 應正規化為 3260
    }
    df = build_pool(TOP5, lambda t: holdings.get(t), min_pool_size=50)
    row = df[df.ticker == "2330"].iloc[0]
    assert row["count"] == 2
    assert row["etf_sources"] == "0050.TW|00878.TW"
    assert "3260" in df.ticker.values
    # count=2 排在 count=1 前
    assert df.iloc[0]["ticker"] == "2330"


def test_build_pool_excludes_etf_self():
    holdings = {"0050.TW": ["0050", "2330"]}   # 含自身代號
    df = build_pool(TOP5[:1], lambda t: holdings.get(t), min_pool_size=50)
    assert "0050" not in df.ticker.values
    assert "2330" in df.ticker.values


def test_build_pool_warns_without_deep():
    holdings = {"0050.TW": [f"{2000+i}.TW" for i in range(10)]}
    df = build_pool([{"ticker": "0050.TW", "name": "A"}],
                    lambda t: holdings.get(t), min_pool_size=50)
    assert len(df) == 10   # 無法補足，log warning 後續行


def test_build_pool_extends_with_deep_fn():
    base = [f"{2000+i}.TW" for i in range(10)]
    deep = [f"{3000+i}.TW" for i in range(45)]     # 延伸後足夠
    calls = []
    def deep_fn(etf, depth):
        calls.append(depth)
        return deep
    df = build_pool([{"ticker": "0050.TW", "name": "A"}],
                    lambda t: base, min_pool_size=50,
                    deep_holdings_fn=deep_fn)
    assert len(df) >= 50
    assert calls, "延伸函式未被呼叫"


def test_build_pool_top1_weight_order_tiebreak():
    # 同 count=1 時，Top1 ETF 內權重順序前者排前
    holdings = {"0050.TW": ["2882.TW", "2330.TW"]}
    df = build_pool(TOP5[:1], lambda t: holdings.get(t), min_pool_size=50)
    assert list(df.ticker)[:2] == ["2882", "2330"]


# ---- rank_etfs ----
class _FakeIdx(pd.DatetimeIndex):
    pass


def test_rank_etfs_sorts_desc():
    cfg = {"etf_candidates": [
        {"ticker": "A.TW", "name": "甲", "category": "市值型"},
        {"ticker": "B.TW", "name": "乙", "category": "高股息"},
    ], "top_n_etf": 1}

    idx = pd.date_range("2023-08-25", periods=2, freq="YE")
    fake = pd.DataFrame({"A.TW": [100.0, 150.0],      # +50%
                         "B.TW": [100.0, 120.0]},     # +20%
                        index=idx)

    top = rank_etfs(cfg, download_fn=lambda ts: fake)
    assert top[0]["ticker"] == "A.TW"
    assert top[0]["return_3y"] == pytest.approx(0.5)


def test_rank_etfs_no_autoadjust_in_default_download():
    import inspect
    from common import universe
    src = inspect.getsource(universe._default_download)
    assert "auto_adjust=False" in src
    assert "Adj Close" not in src
