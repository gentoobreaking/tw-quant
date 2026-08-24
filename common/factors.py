"""找買點管線 — 因子評分模組（T008 因子②；T009/T010 後續擴充）

因子②（30 分）全部使用真實券商共識數據（yfinance），禁止代理指標模擬。
"""
from __future__ import annotations

from typing import Callable, Optional

from .logger import logger


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
        growth_2026 = None
        g26 = row_e.get("growth")
        if g26 is not None and g26 == g26:
            growth_2026 = float(g26)
        eps_2027 = None
        growth_2027 = None
        if ee is not None and "+1y" in getattr(ee, "index", []):
            v = ee.loc["+1y"].get("avg")
            eps_2027 = float(v) if v == v else None
            g27 = ee.loc["+1y"].get("growth")
            if g27 == g27:
                growth_2027 = float(g27)
        target_mean = None
        if isinstance(targets, dict) and targets.get("mean") is not None:
            target_mean = float(targets["mean"])

        return {
            "f2": min(int(f2), 30),
            "eps_2026": eps_2026,
            "eps_2027": eps_2027,
            "growth_2026": growth_2026,
            "growth_2027": growth_2027,
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
        "growth_2026": None, "growth_2027": None,
        "rev_1m": None, "rev_3m": None, "target_mean": None,
        "analysts": 0, "note": f"無覆蓋（{reason}）", "_sub": {},
    }


# ================================================================
# 因子① 基本面成長（25 分）／因子③ 法人籌碼（20 分）——T009
# ================================================================
def _rev_yoy_twse(stock_id: str, cache, rate_limiter) -> Optional[float]:
    """TWSE t187ap05_L 月營收 YoY：取最近 3 個月平均（%）"""
    from .twse import twse_json
    rows = cache.get("pipeline_rev_all", lambda: twse_json(
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        rate_limiter=rate_limiter) or [], ttl=43200)
    yoy = []
    for r in rows:
        if r.get("公司代號") != stock_id:
            continue
        v = r.get("前一年同月增減(%)" ) or r.get("前一年同月增減(%)".replace("(", "("))
        try:
            yoy.append(float(str(v).replace("+", "")))
        except (ValueError, TypeError):
            continue
    return sum(yoy) / len(yoy) if yoy else None


def _rev_yoy_finmind(finmind, stock_id: str) -> Optional[float]:
    """FinMind TaiwanStockMonthRevenue：自行計算近 3 月 YoY 平均"""
    rows = finmind.fetch_dataset("TaiwanStockMonthRevenue", stock_id)
    if not rows:
        return None
    by_month = {r["date"][:7]: float(r["revenue"]) for r in rows}
    dates = sorted(by_month)[-3:]
    vals = []
    for d in dates:
        y, m = int(d[:4]), int(d[5:7])
        prev = f"{y-1}-{m:02d}"
        if prev in by_month and by_month[prev]:
            vals.append((by_month[d] / by_month[prev] - 1) * 100)
    return sum(vals) / len(vals) if vals else None


def _gross_margin_trend(rows: list, stock_id: str = "") -> tuple[Optional[float], Optional[float]]:
    """毛利率（最近季, 近兩季差異 pct 點）；rows 為 FinMind FS 資料"""
    if not rows:
        return None, None
    quarters: dict[str, tuple[float, float]] = {}
    for r in rows:
        t = r.get("type", "")
        q = r.get("date", "")
        if t in ("Revenue", "GrossProfit", "營業收入", "毛利"):
            v = float(r.get("value") or 0)
            rev_g = quarters.setdefault(q, [None, None])
            if t in ("Revenue", "營業收入"):
                rev_g[0] = abs(v)
            else:
                rev_g[1] = abs(v)
    gms = []
    for q in sorted(quarters):
        rev, gp = quarters[q]
        if rev and gp is not None:
            gms.append(gp / rev * 100)
    if len(gms) >= 2:
        return round(gms[-1], 2), round(gms[-1] - gms[-2], 2)
    if len(gms) == 1:
        return round(gms[-1], 2), None
    return None, None


def score_fundamentals(ticker: str, *, cache=None, rate_limiter=None,
                       finmind=None, eps_data: Optional[dict] = None,
                       roe_provider: Optional[Callable[[str], Optional[float]]] = None,
                       fs_provider: Optional[Callable[[str], list]] = None,
                       cf_provider: Optional[Callable[[str], list]] = None,
                       ) -> dict:
    """因子① 基本面成長（25 分）

    EPS成長10＋營收YoY6＋ROE3＋毛利率趨勢2＋FCF4
    """
    def compute() -> dict:
        sub = {}

        # 1. 2026 EPS 預估成長率（10 分）——重用因子②資料
        eps = eps_data or (cache and cache.get(
            f"pipeline_eps_{ticker}",
            lambda: score_eps_revision(ticker), ttl=86400)) \
            or score_eps_revision(ticker)
        g26 = eps.get("growth_2026")
        s1 = _grade(g26 or -999, [(0.30, 10), (0.15, 7), (0.05, 4), (0.0, 2)])
        sub["eps_growth"] = s1

        # 2. 營收 YoY 近 3 月平均（6 分）
        rev_yoy = None
        if rate_limiter is not None and cache is not None:
            try:
                rev_yoy = _rev_yoy_twse(ticker, cache, rate_limiter)
            except Exception as e:  # noqa: BLE001
                logger.debug("t187ap05 失敗：%s", e)
        if rev_yoy is None and finmind is not None:
            try:
                rev_yoy = _rev_yoy_finmind(finmind, ticker)
            except Exception as e:  # noqa: BLE001
                logger.debug("FinMind 營收失敗：%s", e)
        s2 = _grade(rev_yoy or -999, [(20, 6), (10, 4), (0, 2)])
        sub["rev_yoy"] = s2

        # 3. ROE（3 分）：yfinance info 為主，provider 可注入
        roe = None
        try:
            roe_fn = roe_provider or _default_roe
            roe = roe_fn(ticker)
        except Exception:  # noqa: BLE001
            pass
        s3 = _grade(roe or -999, [(0.15, 3), (0.08, 2), (0.0, 1)])
        sub["roe"] = s3

        # 4. 毛利率趨勢（2 分）：FinMind FinancialStatements 近兩季
        gm_q = gm_delta = None
        try:
            fs_fn = fs_provider or (
                (lambda t: finmind.fetch_dataset(
                    "TaiwanStockFinancialStatements", t)) if finmind else None)
            if fs_fn:
                gm_q, gm_delta = _gross_margin_trend(fs_fn(ticker))
        except Exception:  # noqa: BLE001
            pass
        s4 = 0
        if gm_delta is not None:
            s4 = 2 if gm_delta > 0.5 else (1 if gm_delta >= -0.5 else 0)
        elif gm_q is not None:
            s4 = 0   # 只有單季無法判趨勢
        sub["gm_trend"] = s4

        # 5. FCF（4 分）：FinMind CashFlowsStatement 最近季
        fcf_positive = False
        try:
            cf_fn = cf_provider or (
                (lambda t: finmind.fetch_dataset(
                    "TaiwanStockCashFlowsStatement", t)) if finmind else None)
            if cf_fn:
                rows = cf_fn(ticker)
                ocf = capex = None
                for r in rows:
                    t = str(r.get("type", ""))
                    v = float(r.get("value") or 0)
                    if "營業" in t and "現金" in t:
                        ocf = max(v, ocf or v) if ocf is None else v
                    if "固定資產" in t or "資本支出" in t:
                        capex = min(abs(v), capex if capex is not None else abs(v))
                fcf_positive = ocf is not None and ocf > 0
                if capex is not None:
                    fcf_positive = (ocf - capex) > 0
        except Exception:  # noqa: BLE001
            pass
        s5 = 4 if fcf_positive else 0
        sub["fcf"] = s5

        f1 = min(s1 + s2 + s3 + s4 + s5, 25)
        return {
            "f1": f1, "_sub": sub,
            "eps_growth_2026": g26, "rev_yoy_3m": rev_yoy,
            "roe": roe, "gross_margin_q": gm_q,
            "gross_margin_delta": gm_delta,
        }

    if cache is not None:
        return cache.get(f"pipeline_fund_{ticker}", compute, ttl=43200)
    return compute()


def _default_roe(ticker: str) -> Optional[float]:
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    v = yf.Ticker(ticker).get_info().get("returnOnEquity")
    return float(v) if v is not None else None


# ---- 因子③ 籌碼（20 分）----
def _t86_window(stock_id: str, days: int, cache, rate_limiter,
                max_back: int = 45) -> dict[str, list]:
    """抓最近 days 個交易日的 fund/T86 全市場資料，回傳 {date: row}"""
    from .twse import twse_data
    import datetime as dt

    out: dict[str, list] = {}
    end = dt.date.today()
    for back in range(max_back):
        d = end - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        key = f"pipeline_t86_{ds}"
        rows = cache.get(key, lambda ds=ds: twse_data(
            "fund/T86", ds, "", rate_limiter=rate_limiter) or [],
            ttl=86400)
        if rows:
            out[ds] = rows
        if len(out) >= days:
            break
    return out


def _sum_chip(rows_by_date: dict, stock_id: str, idx_foreign: int,
              idx_foreign_self: int, idx_trust: int) -> dict:
    foreign = trust = 0.0
    for ds, rows in rows_by_date.items():
        for row in rows:
            if not row or len(row) <= max(idx_foreign, idx_trust):
                continue
            if row[0] != stock_id:
                continue
            try:
                foreign += float(row[idx_foreign]) + float(row[idx_foreign_self])
                trust += float(row[idx_trust])
            except (ValueError, TypeError):
                continue
    return {"foreign_20d": foreign / 1000, "trust_20d": trust / 1000}   # 張


def score_chips(ticker: str, *, cache=None, rate_limiter=None,
                finmind=None) -> dict:
    """因子③ 法人/主力籌碼（20 分）

    主路徑 TWSE fund/T86；備援 FinMind InstitutionalInvestorsBuySell。
    """
    def compute() -> dict:
        sub = {}
        # 主路徑：T86 近 20 交易日
        window = {}
        if rate_limiter is not None and cache is not None:
            try:
                window = _t86_window(ticker, 20, cache, rate_limiter)
            except Exception:  # noqa: BLE001
                window = {}
        sums = _sum_chip(window, ticker, 4, 7, 10) if window else None

        foreign_5d = foreign_20d = trust_5d = trust_20d = None
        if sums:
            foreign_20d, trust_20d = sums["foreign_20d"], sums["trust_20d"]
        elif finmind is not None:
            logger.info("籌碼主路徑無資料，FinMind 備援啟用（%s）", ticker)
            rows = finmind.fetch_dataset(
                "TaiwanStockInstitutionalInvestorsBuySell", ticker)
            # 長表：date, investor, buy, sell
            agg: dict[str, dict[str, float]] = {}
            for r in rows:
                inv = str(r.get("investor", ""))
                net = (float(r.get("buy") or 0) - float(r.get("sell") or 0))
                a = agg.setdefault(r.get("date", ""), {})
                a[inv] = a.get(inv, 0) + net
            if agg:
                dates = sorted(agg)[-20:]
                foreign_20d = sum(a.get("ForeignInvestor", 0) for a in (agg[d] for d in dates)) / 1000
                trust_20d = sum(a.get("InvestmentTrust", 0) for a in (agg[d] for d in dates)) / 1000

        # 5 日窗口（T86 再取一次較小窗口）
        if rate_limiter is not None and cache is not None:
            w5 = _t86_window(ticker, 5, cache, rate_limiter)
            sums5 = _sum_chip(w5, ticker, 4, 7, 10) if w5 else None
            if sums5:
                foreign_5d, trust_5d = sums5["foreign_20d"], sums5["trust_20d"]

        f3 = 0
        if foreign_5d is not None:
            s = 6 if foreign_5d > 0 else 0
            sub["foreign_5d"] = s; f3 += s
        if foreign_20d is not None:
            s = 4 if foreign_20d > 0 else 0
            sub["foreign_20d"] = s; f3 += s
        if trust_5d is not None:
            s = 4 if trust_5d > 0 else 0
            sub["trust_5d"] = s; f3 += s
        if trust_20d is not None:
            s = 2 if trust_20d > 0 else 0
            sub["trust_20d"] = s; f3 += s
        # 三大法人方向一致（外資與投信同向買超）
        if foreign_20d is not None and trust_20d is not None \
                and foreign_20d > 0 and trust_20d > 0:
            sub["direction"] = 2; f3 += 2

        # 外資持股比率 20 日變化（FinMind）
        holding_up = None
        if finmind is not None:
            try:
                sh = finmind.fetch_dataset("TaiwanStockShareholding", ticker)
                pcts = [(r["date"], float(r.get("used_shareholding_pct") or 0))
                        for r in sh if r.get("used_shareholding_pct") is not None]
                pcts.sort()
                if len(pcts) >= 21:
                    holding_up = pcts[-1][1] - pcts[-21][1] > 0
            except Exception:  # noqa: BLE001
                pass
        if holding_up is not None:
            s = 2 if holding_up else 0
            sub["foreign_holding"] = s; f3 += s

        return {"f3": min(int(f3), 20), "_sub": sub}

    if cache is not None:
        return cache.get(f"pipeline_chips_{ticker}", compute, ttl=43200)
    return compute()
