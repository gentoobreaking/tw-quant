import pytest
import pandas as pd
import numpy as np
from stock_screener import check_tech

@pytest.fixture
def mock_bullish_df():
    """模擬一個多頭趨勢的 DataFrame"""
    dates = pd.date_range(start="2024-01-01", periods=200)
    # 股價穩定上揚
    close = [100 + i * 0.5 for i in range(200)]
    # 成交量放大
    volume = [1000] * 195 + [2000] * 4 + [3000]
    
    df = pd.DataFrame({
        "open": close,
        "high": [c + 1 for c in close],
        "low": [c - 1 for c in close],
        "close": close,
        "volume": volume
    }, index=dates)
    return df

def test_check_tech_bullish(mock_bullish_df):
    """測試多頭趨勢下 C1~C4 是否通過"""
    # 由於 C3 檢查的是前 5 日最低價曾跌破 20MA，我們需要製造一個跌破點
    df = mock_bullish_df.copy()
    
    # 取得當前的 MA20 (大約是最後一天的價格 - 5)
    ma20 = df["close"].rolling(20).mean().iloc[-3]
    # 將 3 天前的最低價設為低於 MA20
    df.iloc[-3, df.columns.get_loc("low")] = ma20 - 2
    
    result = check_tech(df)
    assert bool(result["c1"]) is True  # 收>60MA且向上
    assert bool(result["c2"]) is True  # 收>20MA
    assert bool(result["c3"]) is True  # 前Low跌破過20MA
    assert bool(result["c4"]) is True  # 量>5MA
    assert bool(result["ok"]) is True

def test_check_tech_bearish(mock_bullish_df):
    """測試空頭趨勢下是否失敗"""
    df = mock_bullish_df.copy()
    # 將最後一天價格拉到 60MA 以下
    ma60 = df["close"].rolling(60).mean().iloc[-1]
    df.iloc[-1, df.columns.get_loc("close")] = ma60 - 10
    
    result = check_tech(df)
    assert bool(result["c1"]) is False
    assert bool(result["ok"]) is False
