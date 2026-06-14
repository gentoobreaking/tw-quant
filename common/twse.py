"""TWSE Open API 呼叫"""
import time
import requests

from typing import Optional, List, Dict, Any

TWSE_HEADERS = {"User-Agent": "Mozilla/5.0"}


def twse_json(url: str, retries: int = 3, rate_limiter: Any = None) -> Optional[Dict[str, Any]]:
    """GET 請求 TWSE JSON API，回傳 dict 或 None"""
    for attempt in range(retries):
        if rate_limiter:
            rate_limiter.wait("twse")
        try:
            r = requests.get(url, headers=TWSE_HEADERS, timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                wait = (attempt + 1) * 2
                time.sleep(wait)
                continue
            return r.json() if r.status_code == 200 else None
        except Exception:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 2)
                continue
            return None
    return None


def twse_data(endpoint: str, date: str = "", stock_no: str = "",
              retries: int = 3, rate_limiter: Any = None) -> List[List[Any]]:
    """通用 TWSE API 呼叫，回傳 data list"""
    base = f"https://www.twse.com.tw/{endpoint}"
    params = f"response=json&date={date}&selectType=ALL"
    if stock_no:
        params += f"&stockNo={stock_no}"
    j = twse_json(f"{base}?{params}", retries=retries, rate_limiter=rate_limiter)
    if j and j.get("stat") == "OK":
        data = j.get("data")
        return data if isinstance(data, list) else []
    return []
