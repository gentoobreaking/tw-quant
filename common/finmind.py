"""FinMind v4 REST client — TWSE/yfinance 的備援資料源（T005）

走 REST 直呼，不經 MCP。免費會員約 600 req/hr，
所有請求前必須經過 rate_limiter.wait("finmind")。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import requests

from .logger import logger

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# 管線會用到的資料集白名單（防呆用，非 API 限制）
SUPPORTED_DATASETS = {
    "TaiwanStockPrice",
    "TaiwanStockInstitutionalInvestorsBuySell",
    "TaiwanStockMonthRevenue",
    "TaiwanStockFinancialStatements",
    "TaiwanStockShareholding",
    "TaiwanStockCashFlowsStatement",
}


class FinMindAuthError(RuntimeError):
    """Token 無效或未授權（HTTP 401）"""


class FinMindClient:
    """FinMind v4 資料查詢（thread-safe 需求低，管線為序列批次）"""

    def __init__(self, token: Optional[str] = None,
                 rate_limiter=None, retries: int = 2):
        self._token = token or ""
        self._rate_limiter = rate_limiter
        self._retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})

    def fetch_dataset(self, dataset: str, data_id: Optional[str] = None,
                      start_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> list[dict]:
        """查詢資料集；失敗時回傳 []（不拋出，由呼叫端決定後續）

        例外語意：
        - FinMindAuthError：401 token 問題（重試無義義，直接上拋）
        - 其他錯誤：warning 後回 []，讓 with_fallback 決策
        """
        if dataset not in SUPPORTED_DATASETS:
            logger.debug("FinMind dataset %s 不在白名單，仍嘗試查詢", dataset)

        params: dict = {"dataset": dataset}
        if self._token:
            params["token"] = self._token
        if data_id:
            params["data_id"] = data_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        retried_429 = False
        for attempt in range(self._retries + 1):
            if self._rate_limiter is not None:
                self._rate_limiter.wait("finmind")
            try:
                r = self._session.get(FINMIND_URL, params=params, timeout=20)
            except requests.exceptions.Timeout:
                logger.warning("FinMind %s 逾時（attempt %d/%d）",
                               dataset, attempt + 1, self._retries + 1)
                continue
            except requests.exceptions.RequestException as e:
                logger.warning("FinMind %s 連線失敗：%s",
                               dataset, str(e)[:100])
                continue

            if r.status_code == 200:
                try:
                    payload = r.json()
                except ValueError:
                    logger.warning("FinMind %s 回應非 JSON", dataset)
                    return []
                if payload.get("msg") == "success":
                    return payload.get("data") or []
                # 200 但業務層錯誤（如 msg=status 描述）
                logger.warning("FinMind %s 業務錯誤：%s",
                               dataset, str(payload.get("msg"))[:120])
                return []

            if r.status_code == 401:
                logger.warning("FinMind 401 Unauthorized：token 無效或未填"
                               "（dataset=%s）", dataset)
                raise FinMindAuthError(f"FinMind token 無效（{dataset}）")

            if r.status_code == 429:
                if not retried_429:
                    logger.warning("FinMind 429 限流，sleep 60s 後重試一次")
                    time.sleep(60)
                    retried_429 = True
                    continue
                logger.warning("FinMind 429 重試仍限流（dataset=%s）", dataset)
                return []

            logger.warning("FinMind HTTP %d（dataset=%s）：%s",
                           r.status_code, dataset, r.text[:100])

        return []

    def close(self):
        self._session.close()


def with_fallback(primary_fn: Callable[[], list],
                  fallback_fn: Callable[[], list],
                  label: str = "") -> tuple[list, str]:
    """備援包裝：primary 拋例外或回空值時才呼叫 fallback。

    回傳 (data, source)，source ∈ {"primary", "finmind"}；
    觸發備援時 log「FinMind 備援啟用」（供報表統計）。
    """
    try:
        data = primary_fn()
        if data:
            return data, "primary"
        logger.info("%s 主路徑回空值，FinMind 備援啟用", label or "查詢")
    except Exception as e:  # noqa: BLE001 —— 備援設計本意就是吞掉主路徑任何失敗
        logger.info("%s 主路徑失敗（%s），FinMind 備援啟用",
                    label or "查詢", str(e)[:80])
    data = fallback_fn()
    return data, "finmind"
