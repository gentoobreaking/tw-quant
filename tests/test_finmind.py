"""T005 單元測試 — FinMind client 四路徑與 with_fallback 行為"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from common.finmind import FinMindAuthError, FinMindClient, with_fallback


def make_response(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if payload is not None:
        r.json.return_value = payload
    else:
        r.json.side_effect = ValueError("not json")
    return r


def make_client() -> FinMindClient:
    rl = MagicMock()
    return FinMindClient(token="dummy", rate_limiter=rl, retries=2)


# 註：FinMindClient 內部用 self._session = requests.Session()，
# 因此直接 patch requests.Session.get 即可攔截所有請求。

 
# ---- 200 成功 ----
def test_fetch_200_success():
    c = make_client()
    resp = make_response(200, {"msg": "success", "status": 200,
                               "data": [{"close": 2375.0}]})
    with patch("requests.Session.get", return_value=resp):
        data = c.fetch_dataset("TaiwanStockPrice", "2330",
                               "2026-08-20", "2026-08-22")
    assert data == [{"close": 2375.0}]


# ---- 401：拋 FinMindAuthError ----
def test_fetch_401_raises():
    c = make_client()
    resp = make_response(401, None, "unauthorized")
    with patch("requests.Session.get", return_value=resp):
        with pytest.raises(FinMindAuthError):
            c.fetch_dataset("TaiwanStockPrice", "2330")


# ---- 429：sleep 60 重試一次後成功 ----
def test_fetch_429_retry_once_then_success():
    c = make_client()
    r429 = make_response(429, None, "rate limited")
    rok = make_response(200, {"msg": "success", "data": [{"x": 1}]})
    with patch("requests.Session.get", side_effect=[r429, rok]), \
         patch("common.finmind.time.sleep") as msleep:
        data = c.fetch_dataset("TaiwanStockPrice", "2330")
    assert data == [{"x": 1}]
    msleep.assert_called_once_with(60)


# ---- timeout：warning 後重試，最終回 [] ----
def test_fetch_timeout_returns_empty():
    c = make_client()
    with patch("requests.Session.get",
               side_effect=requests.exceptions.Timeout("t")), \
         patch("common.finmind.time.sleep"):
        data = c.fetch_dataset("TaiwanStockPrice", "2330")
    assert data == []


# ---- with_fallback 三情境 ----
def test_fallback_primary_ok():
    data, source = with_fallback(lambda: [1], lambda: [2], label="測試")
    assert (data, source) == ([1], "primary")


def test_fallback_primary_empty():
    data, source = with_fallback(lambda: [], lambda: [2], label="測試")
    assert (data, source) == ([2], "finmind")


def test_fallback_primary_raises():
    def boom():
        raise RuntimeError("twse down")
    data, source = with_fallback(boom, lambda: [3], label="測試")
    assert (data, source) == ([3], "finmind")
