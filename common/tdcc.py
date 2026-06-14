"""TDCC 集保查詢 — session 管理、大戶比率、頁面結構變更偵測（thread-safe）"""
import re
import threading
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .rate_limit import RateLimiter
from .logger import logger

_TDCC_STRUCTURE_FINGERPRINTS = {
    "title": "股權分散表",
    "token_name": "SYNCHRONIZER_TOKEN",
    "uri_name": "SYNCHRONIZER_URI",
    "form_method": "submit",
    "select_name": "scaDate",
    "min_tables": 2,
    "table_cells": 5,
    "token_pattern": r"^[a-zA-Z0-9\-_]{16,}$",
}


class TDCCQuery:
    def __init__(self, rate_limiter: RateLimiter, retries: int = 3,
                 large_share_threshold: int = 1_000_000):
        self._session: Optional[requests.Session] = None
        self._rate_limiter = rate_limiter
        self._retries = retries
        self._threshold = large_share_threshold
        self._cache = {}
        self._lock = threading.Lock()
        self._structural_warned = False

    def _init_session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Mozilla/5.0"})

    def _check_page_structure(self, soup) -> list[str]:
        issues = []
        title = soup.find("title")
        if title is None:
            issues.append("missing <title>")
        elif _TDCC_STRUCTURE_FINGERPRINTS["title"] not in title.get_text():
            issues.append(f"title='{title.get_text()}' (expected contains '{_TDCC_STRUCTURE_FINGERPRINTS['title']}')")

        t_input = soup.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["token_name"]})
        if t_input is None:
            issues.append(f"missing input[name={_TDCC_STRUCTURE_FINGERPRINTS['token_name']}]")
        else:
            val = t_input.get("value", "")
            if not re.match(_TDCC_STRUCTURE_FINGERPRINTS["token_pattern"], str(val)):
                issues.append(f"SYNCHRONIZER_TOKEN unexpected format (len={len(str(val))})")

        u_input = soup.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["uri_name"]})
        if u_input is None:
            issues.append(f"missing input[name={_TDCC_STRUCTURE_FINGERPRINTS['uri_name']}]")

        tbls = soup.find_all("table")
        if len(tbls) < _TDCC_STRUCTURE_FINGERPRINTS["min_tables"]:
            issues.append(f"tables={len(tbls)} < {_TDCC_STRUCTURE_FINGERPRINTS['min_tables']}")
        elif len(tbls) >= 2:
            rows = tbls[1].find_all("tr")
            sample_cells = []
            for row in rows:
                cells = row.find_all(["td", "th"])
                if cells:
                    sample_cells = cells
                    break
            if sample_cells and len(sample_cells) != _TDCC_STRUCTURE_FINGERPRINTS["table_cells"]:
                issues.append(f"table[1] cols={len(sample_cells)} (expected {_TDCC_STRUCTURE_FINGERPRINTS['table_cells']})")

        sel = soup.find("select", {"name": _TDCC_STRUCTURE_FINGERPRINTS["select_name"]})
        if sel is None:
            issues.append(f"missing select[name={_TDCC_STRUCTURE_FINGERPRINTS['select_name']}]")

        return issues

    def _warn_structure(self, context: str, soup, issues: list[str]):
        if not issues:
            return
        snippet = str(soup)[:300] if soup else "None"
        msg = (f"TDCC 頁面結構異常 [{context}]: {'; '.join(issues)}")
        logger.warning(msg)
        logger.debug(f"HTML snippet: {snippet}")

    def query(self, stock_no: str, date_str: str, token: str = "", uri: str = ""
              ) -> tuple[Optional[float], str, str]:
        with self._lock:
            return self._query_impl(stock_no, date_str, token, uri)

    def _query_impl(self, stock_no: str, date_str: str, token: str = "", uri: str = ""
              ) -> tuple[Optional[float], str, str]:
        self._init_session()
        cache_key = (stock_no, date_str)

        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if cached is not None:
                return cached[0], cached[1], cached[2]
            return None, token, uri

        if not token:
            self._rate_limiter.wait("tdcc")
            r = None
            for retry in range(self._retries):
                try:
                    r = self._session.get(
                        "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", timeout=15)
                    if r.status_code == 200:
                        break
                except Exception as e:
                    logger.debug(f"TDCC GET 失敗 (retry {retry}): {e}")
                if retry < self._retries - 1:
                    time.sleep((retry + 1) * 2)
                r = None
            if r is None:
                logger.warning(f"TDCC GET 失敗 (stock={stock_no}, date={date_str})")
                return None, token, uri
            soup = BeautifulSoup(r.text, "lxml")

            issues = self._check_page_structure(soup)
            self._warn_structure("GET", soup, issues)

            el_t = soup.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["token_name"]})
            el_u = soup.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["uri_name"]})
            if el_t is None or el_u is None:
                logger.warning(f"TDCC GET 無 token/uri (stock={stock_no}, date={date_str})")
                return None, token, uri
            token = el_t["value"]
            uri = el_u["value"]

        fd = {
            _TDCC_STRUCTURE_FINGERPRINTS["token_name"]: token,
            _TDCC_STRUCTURE_FINGERPRINTS["uri_name"]: uri,
            "method": _TDCC_STRUCTURE_FINGERPRINTS["form_method"],
            "firDate": date_str,
            "scaDate": date_str,
            "sqlMethod": "StockNo",
            "stockNo": stock_no,
        }

        r2 = None
        for attempt in range(self._retries):
            self._rate_limiter.wait("tdcc")
            try:
                r2 = self._session.post(
                    "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
                    data=fd, timeout=15)
                if r2.status_code == 200:
                    break
            except Exception as e:
                logger.debug(f"TDCC POST 失敗 (retry {attempt}): {e}")
            if attempt < self._retries - 1:
                time.sleep((attempt + 1) * 3)
            r2 = None

        if r2 is None:
            logger.warning(f"TDCC POST 失敗 (stock={stock_no}, date={date_str})")
            self._cache[cache_key] = None
            return None, token, uri

        try:
            soup2 = BeautifulSoup(r2.text, "lxml")

            issues = self._check_page_structure(soup2)
            self._warn_structure("POST result", soup2, issues)

            new_token = (soup2.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["token_name"]}) or {}).get("value", "")
            new_uri = (soup2.find("input", {"name": _TDCC_STRUCTURE_FINGERPRINTS["uri_name"]}) or {}).get("value", "")
            tbls = soup2.find_all("table")
            if len(tbls) < _TDCC_STRUCTURE_FINGERPRINTS["min_tables"]:
                self._cache[cache_key] = None
                return None, new_token or token, new_uri or uri

            total = 0.0
            for row in tbls[1].find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) != _TDCC_STRUCTURE_FINGERPRINTS["table_cells"]:
                    continue
                m = re.match(r"([\d,]+)", cells[1])
                if m and int(m.group(1).replace(",", "")) >= self._threshold:
                    total += float(cells[4].replace("%", "").replace(",", ""))

            self._cache[cache_key] = [total, new_token or token, new_uri or uri]
            return total, new_token or token, new_uri or uri
        except Exception as e:
            logger.warning(f"TDCC POST 解析失敗 (stock={stock_no}, date={date_str}): {e}")
            self._cache[cache_key] = None
            return None, token, uri

    def available_dates(self) -> list[str]:
        with self._lock:
            return self._available_dates_impl()

    def _available_dates_impl(self) -> list[str]:
        self._init_session()
        try:
            r = self._session.get(
                "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", timeout=15)
            soup = BeautifulSoup(r.text, "lxml")

            issues = self._check_page_structure(soup)
            self._warn_structure("available_dates", soup, issues)

            sel = soup.find("select", {"name": _TDCC_STRUCTURE_FINGERPRINTS["select_name"]})
            if sel is None:
                logger.warning("TDCC available_dates: 找不到 scaDate select")
                return []
            dates = [opt["value"] for opt in sel.find_all("option") if opt.get("value")]
            invalid = [d for d in dates if not re.match(r"^\d{8}$", d)]
            if invalid:
                logger.warning(f"TDCC 日期格式異常 (前5筆): {invalid[:5]}")
            return dates
        except Exception as e:
            logger.warning(f"TDCC available_dates 失敗: {e}")
            return []

    def load_from_disk_cache(self, disk_cache, ttl: int):
        with self._lock:
            self._load_from_disk_cache_impl(disk_cache, ttl)

    def _load_from_disk_cache_impl(self, disk_cache, ttl: int):
        now = time.time()
        dc = disk_cache.load_disk_cache()
        loaded = 0
        for k, v in dc.items():
            if k.startswith("tdcc_"):
                parts = k.split("_", 2)
                if len(parts) == 3 and isinstance(v, dict):
                    ts = v.get("_ts", 0)
                    if now - ts < ttl:
                        self._cache[(parts[1], parts[2])] = v.get("data")
                        loaded += 1
        if loaded:
            logger.info(f"TDCC 載入 {loaded} 筆磁碟快取")

    def save_to_disk_cache(self, disk_cache):
        with self._lock:
            self._save_to_disk_cache_impl(disk_cache)

    def _save_to_disk_cache_impl(self, disk_cache):
        dc = disk_cache.load_disk_cache()
        now = time.time()
        saved = 0
        for (stock, date), val in self._cache.items():
            key = f"tdcc_{stock}_{date}"
            dc[key] = {"data": val, "_ts": now}
            saved += 1
        disk_cache.save_disk_cache(dc)
        logger.info(f"TDCC 寫入 {saved} 筆磁碟快取")
