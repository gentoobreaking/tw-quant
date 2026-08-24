"""T007 單元測試 — MoneyDJ 解析與 build_sector_map 行為"""
from unittest.mock import MagicMock, patch

import pytest

from common.moneydj import (build_sector_map, fetch_industry_index,
                            parse_industry_page)

ZHA_HTML = '<a href="/z/zh/zha/zh00.djhtm?a=C023326">半導體製程設備</a>' \
           '<a href="/z/zh/zha/zh00.djhtm?a=C012017">罐頭業</a>'
PAGE_HTML = '<a href="javascript:Link2Stk(\'AS2330\');">2330台積電</a>' \
            '<a href="javascript:Link2Stk(\'AS3260\');">3260台星科</a>'


def test_parse_industry_page():
    assert parse_industry_page(PAGE_HTML) == ["2330", "3260"]


def test_fetch_industry_index_big5():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = ZHA_HTML.encode("big5")
    session.get.return_value = resp
    index = fetch_industry_index(session)
    assert ("C023326", "半導體製程設備") in index
    assert len(index) == 2


def test_build_sector_map_early_stop_and_cache():
    rl = MagicMock()
    cache = MagicMock()
    cache.get.side_effect = lambda key, fn, **kw: fn()   # 永遠掃描

    pages = {
        "C1": PAGE_HTML,                                   # 含 2330、3260
        "C2": "no stocks here",
    }
    idx_html = ('<a href="/z/zh/zha/zh00.djhtm?a=C1">半導體</a>'
                '<a href="/z/zh/zha/zh00.djhtm?a=C2">其他</a>')

    def fake_get(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("ZHA.djhtm"):
            resp.content = idx_html.encode("big5")
        else:
            code = url.split("a=")[1]
            resp.content = pages.get(code, "").encode("big5")
        return resp

    with patch("common.moneydj.requests.Session") as FakeSession:
        s = FakeSession.return_value
        s.get.side_effect = fake_get
        mapping = build_sector_map(["2330"], rl, cache)

    assert mapping == {"2330": "半導體"}
    # 提早停止：C2 不該被掃（已命中全部）
    assert s.get.call_count == 2   # ZHA + C1


def test_build_sector_map_uses_cache():
    rl = MagicMock()
    cache = MagicMock()
    cache.get.return_value = {"2330": "半導體"}
    mapping = build_sector_map(["2330"], rl, cache)
    assert mapping == {"2330": "半導體"}
    cache.get.assert_called_once()
