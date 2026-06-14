from typing import Optional
import re
import json

import requests
from bs4 import BeautifulSoup

from .cache import DiskCache
from .rate_limit import RateLimiter
from .yf_utils import get_exchange

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def fetch_top10_holdings(ticker: str, cache: DiskCache, rate_limiter: RateLimiter,
                         retries: int = 2) -> Optional[list[str]]:
    exchange = get_exchange(ticker)
    url = f"https://tw.stock.yahoo.com/quote/{ticker}.{exchange}/holding"
    ck = f"yahoo_holdings_{ticker}"

    def _fetch():
        for attempt in range(retries):
            rate_limiter.wait("yf")
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.content, "html.parser")
                script_tag = soup.find("script", string=re.compile(r"root\.App\.main"))
                if not script_tag:
                    continue
                script = script_tag.string
                match = re.search(r"root\.App\.main\s+=\s+", script)
                if not match:
                    continue
                start = match.end()
                depth = 0
                for i, c in enumerate(script[start:]):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    if depth == 0:
                        end_idx = start + i + 1
                        break
                raw = script[start:end_idx]
                cleaned = re.sub(r"\bundefined\b", "null", raw)
                cleaned = re.sub(r"\bNaN\b", "null", cleaned)
                cleaned = re.sub(r"\bInfinity\b", "null", cleaned)
                parsed = json.loads(cleaned)
                holdings = parsed["context"]["dispatcher"]["stores"]["QuoteETFStore"] \
                    ["etfInfo"]["data"]["portfolio"]["top10Holdings"]["holdingDetail"]
                result = []
                for h in holdings[:10]:
                    sym = h.get("ticker", "")
                    if sym:
                        # Yahoo 股市用 xxxO 表示上櫃股（如 3260O → 3260）
                        sym = sym.replace(".TW", "").replace(".TWO", "")
                        if sym.endswith("O") and sym[:-1].isdigit():
                            sym = sym[:-1]
                        result.append(sym)
                if result:
                    return result
            except Exception:
                if attempt < retries - 1:
                    import time
                    time.sleep((attempt + 1) * 2)
        return None

    return cache.get(ck, _fetch, ttl=86400)