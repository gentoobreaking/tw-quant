"""找買點管線 — 因子評分模組（T008 因子②；T009/T010 後續擴充）

因子②（30 分）全部使用真實券商共識數據（yfinance），禁止代理指標模擬。
"""
from __future__ import annotations

from typing import Callable, Optional


def _default_provider(ticker: str) -> dict:
    """從 yfinance 抓取預估數據（可注入替換以便測試）"""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    t = yf.Ticker(ticker)
    return {
        "earnings_estimate": t.get_earnings_estimate(),
        "eps_trend": t.get_eps_trend(),
        "eps_revisions": t.get_eps_revisions(),
        "growth_estimates": t.get_growth_estimates(),
        "analyst_price_targets": t.get_analyst_price_targets(),
    }


def _grade(value: float, bands: list[tuple[float, int]]) -> int:
    """區間計分：bands 由大到小 [(門檻, 分數)]，最後一項視為 (<門檻, 分數)"""
    for threshold, score in bands:
        if value >= threshold:
            return score
    return 0


def score_eps_revision(ticker: str, cache=None,
                       provider: Optional[Callable[[str], dict]] = None,
                       ) -> dict:
    """因子② EPS 預估上修（30 分）

    回傳 {f2, eps_2026, eps_2027, rev_1m, rev_3m, target_mean,
          analysts, note}
    """
    def compute() -> dict:
        provider_fn = provider or _default_provider
        try:
            raw = provider_fn(ticker)
        except Exception as e:  # noqa: BLE001
            return _no_coverage(str(e)[:80])

        ee = raw.get("earnings_estimate")
        trend = raw.get("eps_trend")
        revisions = raw.get("eps_revisions")
        growth = raw.get("growth_estimates")
        targets = raw.get("analyst_price_targets")

        # 分析師覆蓋檢查
        analysts = 0
        if ee is not None and ee is not False and "0y" in getattr(ee, "index", []):
            n = ee.loc["0y"].get("numberOfAnalysts")
            analysts = int(n) if n == n and n is not None else 0   # NaN 安全
        if analysts < 3 or trend is None or trend is False \
                or "0y" not in getattr(trend, "index", []):
            return _no_coverage("分析師覆蓋不足")

        row_e = ee.loc["0y"]
        row_t = trend.loc["0y"]

        def pct(new, old):
            if old is None or old != old or old == 0:
                return None
            return round((new - old) / abs(old) * 100, 2)

        rev_1m = pct(row_t.get("current"), row_t.get("30daysAgo"))
        rev_3m = pct(row_t.get("current"), row_t.get("90daysAgo"))

        f2 = 0
        sub = {}

        # 子項1：1M 上修幅度（12 分）
        s1 = 0
        if rev_1m is not None:
            s1 = _grade(rev_1m, [(5, 12), (2, 9), (0, 6)])
        sub["rev_1m"] = s1
        f2 += s1

        # 子項2：3M 上修幅度（8 分）
        s2 = 0
        if rev_3m is not None:
            s2 = _grade(rev_3m, [(10, 8), (5, 6), (0, 3)])
        sub["rev_3m"] = s2
        f2 += s2

        # 子項3：上修/下修動能（6 分）
        s3 = 0
        try:
            rrow = revisions.loc["0y"]
            up = int(rrow.get("upLast30days") or 0)
            down = int(rrow.get("downLast30days") or 0)
            if up >= down and up >= 5:
                s3 = 6
            elif up > down:
                s3 = 4
            elif up == down:
                s3 = 2
            sub["revisions"] = s3
            f2 += s3
        except (KeyError, TypeError, ValueError):
            pass

        # 子項4：相對產業預估（4 分）
        s4 = 0
        try:
            if growth is not None and growth is not False \
                    and "0y" in getattr(growth, "index", []):
                g = growth.loc["0y"]
                st, it = g.get("stockTrend"), g.get("indexTrend")
                if st is not None and it is not None and st == st and it == it:
                    s4 = 4 if st > it else 0
                    sub["industry_rel"] = s4
                    f2 += s4
        except (KeyError, TypeError):
            pass

        eps_2026 = float(row_e.get("avg")) if row_e.get("avg") == row_e.get("avg") else None
        eps_2027 = None
        if ee is not None and "+1y" in getattr(ee, "index", []):
            v = ee.loc["+1y"].get("avg")
            eps_2027 = float(v) if v == v else None
        target_mean = None
        if isinstance(targets, dict) and targets.get("mean") is not None:
            target_mean = float(targets["mean"])

        return {
            "f2": min(int(f2), 30),
            "eps_2026": eps_2026,
            "eps_2027": eps_2027,
            "rev_1m": rev_1m,
            "rev_3m": rev_3m,
            "target_mean": target_mean,
            "analysts": analysts,
            "note": "",
            "_sub": sub,
        }

    if cache is not None:
        return cache.get(f"pipeline_eps_{ticker}", compute, ttl=86400)
    return compute()


def _no_coverage(reason: str) -> dict:
    return {
        "f2": 0, "eps_2026": None, "eps_2027": None,
        "rev_1m": None, "rev_3m": None, "target_mean": None,
        "analysts": 0, "note": f"無覆蓋（{reason}）", "_sub": {},
    }
