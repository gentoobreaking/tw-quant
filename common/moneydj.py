"""MoneyDJ 產業分類抓取（T007）

策略：懶掃描——逐產業頁建立 stock_no→industry 映射，
股票池全數命中即提早停止；結果快取 30 天。
ZHA 頁與產業頁皆為 Big5 編碼。
"""
from __future__ import annotations

import re
from typing import Optional

import requests

from .logger import logger
from .tls_fallback import make_twca_session

INDEX_URL = "https://www.moneydj.com/Z/ZH/ZHA/ZHA.djhtm"
PAGE_URL = "https://www.moneydj.com/z/zh/zha/zh00.djhtm?a={code}"

_RE_INDUSTRY_LINK = re.compile(
    r'href="/z/zh/zha/zh00\.djhtm\?a=(C\d+)"[^>]*>([^<]{1,20})<')
_RE_STOCK_LINK = re.compile(r"Link2Stk\('AS(\d{4})'\)")


def _get_big5(session: requests.Session, url: str,
              timeout: int = 20) -> Optional[str]:
    try:
        resp = session.get(url, headers={"User-Agent": "Mozilla/5.0"},
                           timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.warning("MoneyDJ GET 失敗 %s：%s", url, str(e)[:80])
        return None
    if resp.status_code != 200:
        logger.warning("MoneyDJ HTTP %d：%s", resp.status_code, url)
        return None
    return resp.content.decode("big5", errors="ignore")


def fetch_industry_index(session: requests.Session) -> list[tuple[str, str]]:
    """解析 ZHA 索引頁 → [(產業代碼 C######, 產業名稱)]"""
    html = _get_big5(session, INDEX_URL)
    if html is None:
        return []
    seen: dict[str, str] = {}
    for code, name in _RE_INDUSTRY_LINK.findall(html):
        name = name.strip()
        if code not in seen and name:
            seen[code] = name
    logger.info("MoneyDJ 產業索引：%d 個分類", len(seen))
    return list(seen.items())


def parse_industry_page(html: str) -> list[str]:
    """解析單一產業頁的成分股代號（Link2Stk('AS####')）"""
    return _RE_STOCK_LINK.findall(html)


def build_sector_map(tickers: list[str], rate_limiter, cache,
                     session: Optional[requests.Session] = None,
                     max_pages: Optional[int] = None) -> dict[str, str]:
    """懶掃描產業頁建立 ticker→industry；快取 key=pipeline_moneydj_map

    - 每頁前等待 rate_limiter.wait('moneydj')（≥2s）
    - 股票池全數命中或掃完全部頁面即停
    - 回傳可能不含全部 tickers（其餘由呼叫端 fallback 補）
    """
    session = session or make_twca_session()

    def _scan() -> dict:
        index = fetch_industry_index(session)
        if not index:
            return {}
        wanted = set(tickers)
        mapping: dict[str, str] = {}
        scanned = 0
        for code, name in index:
            if max_pages is not None and scanned >= max_pages:
                break
            rate_limiter.wait("moneydj")
            html = _get_big5(session, PAGE_URL.format(code=code))
            scanned += 1
            if html is None:
                continue
            for stock_no in parse_industry_page(html):
                mapping.setdefault(stock_no, name.strip())
            if scanned % 100 == 0:
                logger.info("MoneyDJ 掃描進度：%d/%d 頁，已命中 %d/%d 檔",
                            scanned, len(index),
                            len(wanted & set(mapping)), len(wanted))
            if wanted <= set(mapping):
                logger.info("MoneyDJ 提早命中全部 %d 檔（掃 %d 頁）",
                            len(wanted), scanned)
                break
        logger.info("MoneyDJ 掃描完成：%d 頁，映射 %d 檔，池內命中 %d/%d",
                    scanned, len(mapping), len(wanted & set(mapping)),
                    len(wanted))
        # 空結果不可入快取（會污染 30 天）
        return mapping if mapping else None

    ttl = 30 * 86400
    mapping = cache.get("pipeline_moneydj_map", _scan, ttl=ttl,
                        skip_none=True) or {}
    # 快取可能是舊版部分資料：僅回傳池內需要的部分
    return {t: mapping[t] for t in tickers if t in mapping}
