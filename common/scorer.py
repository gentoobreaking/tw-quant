"""T011 評分加總器 — 兩份 100 分量化表與稽核明細"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .factors import score_chips, score_eps_revision, score_fundamentals, \
    score_momentum, score_position
from .targets import compute_targets
from .logger import logger

RESULT_DIR = Path(__file__).resolve().parent.parent / "screening_results"

# 欄位計算說明（稽核附錄）的單一資料源：
# 6-A 公式總表由此渲染；6-B 明細欄位順序亦以此為準（兩者結構性一致）
FORMULA_TABLE: list[tuple[str, str, str]] = [
    # ---- 原始數值 ----
    ("rev_1m_pct", "(current−30daysAgo)/abs(30daysAgo)×100（0y）",
     "yfinance eps_trend"),
    ("rev_3m_pct", "(current−90daysAgo)/abs(90daysAgo)×100（0y）",
     "yfinance eps_trend"),
    ("up30d / down30d", "近30日分析師上修/下修人次（0y）",
     "yfinance eps_revisions"),
    ("eps_growth_2026", "2026 預估 EPS 成長率", "yfinance earnings_estimate"),
    ("rev_yoy_3m_avg", "最近 3 個月營收 YoY 平均%", "TWSE t187ap05_L／FinMind 備援"),
    ("roe", "最近季股東權益報酬率", "yfinance info returnOnEquity／FinMind"),
    ("gross_margin_q", "最近季毛利率＝毛利額/營收×100", "FinMind FinancialStatements"),
    ("gross_margin_delta", "毛利率近兩季差異（pct 點）", "同上"),
    ("fcf_positive", "自由現金流>0（營業現金流−資本支出）", "FinMind CashFlowsStatement"),
    ("foreign_5d_amt", "外資近 5 交易日淨買超（張）", "TWSE fund/T86／FinMind 備援"),
    ("foreign_20d_amt", "外資近 20 交易日淨買超（張）", "同上"),
    ("trust_5d_amt", "投信近 5 交易日淨買超（張）", "同上"),
    ("trust_20d_amt", "投信近 20 交易日淨買超（張）", "同上"),
    ("main_force_20d", "主力買賣超＝投信+外資 20 日合計淨買超（張）", "衍生"),
    ("close / ma20 / ma60", "收盤價、20/60 日均線（未還原日線 rolling）", "yfinance"),
    ("dist_60d_high_pct", "(close/max60日高−1)×100", "日線"),
    ("dd60_pct / dd120_pct", "距 60/120 日高回撤 %", "日線"),
    ("pos_52w", "收盤在 52W 高低區間位置（0=最低,1=最高）", "日線"),
    ("rsi14", "RSI(14) Wilder 平滑", "日線"),
    # ---- 子項得分 ----
    ("eps_growth / rev_yoy / roe / gm_trend / fcf", "因子①五子項得分（10/6/3/2/4，合計25）", "見上"),
    ("f2_rev_1m / f2_rev_3m / f2_revisions / f2_industry_rel", "因子②四子項得分（12/8/6/4，合計30）", "見上"),
    ("f3_foreign_5d … f3_foreign_holding", "因子③六子項得分（6/4/4/2/2/2，合計20）", "見上"),
    ("f4_above_ma20 … f4_relative", "因子④五子項得分（各3分，合計15）", "見上"),
    ("f5_dd60 … f5_stop_confirm", "因子⑤四子項得分（3/3/2/2，合計10）", "見上"),
    # ---- 衍生買點欄位 ----
    ("entry_low / entry_high", "[20MA, 20MA×1.03]；資格=現價>60MA、20MA向上且>60MA排列", "日線 MA"),
    ("stop_loss", "min(中值×0.93, 發動K棒最低×0.995)；邏輯停損=破60MA+KD死叉", "日線"),
    ("target_price", "min(60日高, 分析師mean) 取離現價較近者；漲幅<8% 改較遠者", "yfinance price_targets"),
    ("rr", "(目標價−進場區中值)/(進場區中值−停損)，門檻 S≥2.0/A≥1.5", "衍生"),
    ("total / 各因子分項", "五因子加總（上限100）", "衍生"),
]

FULL_COLUMNS = ["ticker", "name", "sector", "total",
                "eps_2026", "eps_2027", "rev_1m", "rev_3m",
                "foreign_20d", "main_force_20d",
                "dist_60d_high", "ma20", "ma60", "close",
                "entry_low", "entry_high", "stop_loss",
                "target_price", "rr"]


def run_scoring(universe: pd.DataFrame, cfg: dict, cache,
                rate_limiter=None, finmind=None) -> tuple[
        pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """回傳（表一全量、表二 Top10、稽核明細）三個 DataFrame"""
    rows, details = [], []
    tickers = universe["ticker"].tolist()
    for i, (_, urow) in enumerate(universe.iterrows()):
        t = str(urow["ticker"])
        try:
            eps = score_eps_revision(t, cache=cache)
            fund = score_fundamentals(t, cache=cache,
                                      rate_limiter=rate_limiter,
                                      finmind=finmind,
                                      eps_data=eps)
            chips = score_chips(t, cache=cache, rate_limiter=rate_limiter,
                                finmind=finmind)
            mom = score_momentum(t, cache=cache)
            pos = score_position(t, cache=cache)
            tgt = compute_targets(mom, eps, cfg.get("rr_thresholds"))
        except Exception as e:  # noqa: BLE001 —— 單檔失敗不拖垮整批
            logger.warning("[%d/%d] %s 評分失敗：%s", i + 1, len(tickers),
                           t, str(e)[:100])
            continue

        total = min(fund["f1"] + eps["f2"] + chips["f3"]
                    + mom["f4"] + pos["f5"], 100)
        row = {
            "ticker": t, "name": urow.get("name", ""),
            "sector": urow.get("sector", ""),
            "total": total,
            "f1": fund["f1"], "f2": eps["f2"], "f3": chips["f3"],
            "f4": mom["f4"], "f5": pos["f5"],
            "count": int(urow.get("count", 0)),
            "eps_2026": eps["eps_2026"], "eps_2027": eps["eps_2027"],
            "growth_2026": eps.get("growth_2026"),
            "growth_2027": eps.get("growth_2027"),
            "rev_yoy_3m": fund.get("rev_yoy_3m"),
            "rev_1m": eps["rev_1m"], "rev_3m": eps["rev_3m"],
            "analysts": eps["analysts"], "note": eps["note"],
            "target_mean": eps["target_mean"],
            "foreign_20d": chips.get("foreign_20d"),
            "trust_20d": chips.get("trust_20d"),
            "main_force_20d": (
                round(chips["foreign_20d"] + chips["trust_20d"], 1)
                if chips.get("foreign_20d") is not None
                and chips.get("trust_20d") is not None else None),
            "close": mom.get("close"), "ma20": mom.get("ma20"),
            "ma60": mom.get("ma60"),
            "dist_60d_high": mom.get("dist_60d_high"),
            "rsi14": mom.get("rsi14"),
            "entry_low": tgt["entry_low"], "entry_high": tgt["entry_high"],
            "stop_loss": tgt["stop_loss"],
            "target_price": tgt["target_price"], "rr": tgt["rr"],
            "target_note": tgt["target_note"],
        }
        rows.append(row)
        # 稽核明細：原始數值＋子項得分（欄位順序＝FORMULA_TABLE）
        detail = {"ticker": t}
        raw = {
            "rev_1m_pct": eps["rev_1m"], "rev_3m_pct": eps["rev_3m"],
            "up30d": eps.get("up30d"), "down30d": eps.get("down30d"),
            "eps_growth_2026": fund.get("eps_growth_2026"),
            "rev_yoy_3m_avg": fund.get("rev_yoy_3m"),
            "roe": fund.get("roe"),
            "gross_margin_q": fund.get("gross_margin_q"),
            "gross_margin_delta": fund.get("gross_margin_delta"),
            "fcf_positive": fund.get("fcf_positive"),
            "foreign_5d_amt": chips.get("foreign_5d"),
            "foreign_20d_amt": chips.get("foreign_20d"),
            "trust_5d_amt": chips.get("trust_5d"),
            "trust_20d_amt": chips.get("trust_20d"),
            "main_force_20d": row.get("main_force_20d"),
            "close": mom.get("close"), "ma20": mom.get("ma20"),
            "ma60": mom.get("ma60"),
            "dist_60d_high_pct": mom.get("dist_60d_high"),
            "dd60_pct": pos.get("dd60_pct"), "dd120_pct": pos.get("dd120_pct"),
            "pos_52w": pos.get("pos_52w"), "rsi14": mom.get("rsi14"),
            "entry_low": tgt["entry_low"], "entry_high": tgt["entry_high"],
            "stop_loss": tgt["stop_loss"],
            "target_price": tgt["target_price"], "rr": tgt["rr"],
        }
        detail.update(raw)
        detail.update({k: v for k, v in fund["_sub"].items()})
        detail.update({f"f2_{k}": v for k, v in eps["_sub"].items()})
        detail.update({f"f3_{k}": v for k, v in chips["_sub"].items()})
        detail.update({f"f4_{k}": int(v) if isinstance(v, bool) else v
                       for k, v in mom["_sub"].items()})
        detail.update({f"f5_{k}": int(v) if isinstance(v, bool) else v
                       for k, v in pos["_sub"].items()})
        detail["total"] = total
        details.append(detail)

        if (i + 1) % 10 == 0:
            logger.info("評分進度 %d/%d", i + 1, len(tickers))

    full = pd.DataFrame(rows).sort_values(
        ["total", "f2", "count"], ascending=[False, False, False]
    ).reset_index(drop=True)
    top10 = full.head(10).copy()
    return full, top10, pd.DataFrame(details)


def write_reports(full: pd.DataFrame, top10: pd.DataFrame,
                  details: pd.DataFrame,
                  rejected: pd.DataFrame | None = None,
                  stats: dict | None = None,
                  top5: pd.DataFrame | None = None,
                  out_dir: Path = RESULT_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    md_path = out_dir / f"pipeline_{stamp}.md"
    csv_full = out_dir / f"pipeline_{stamp}_full.csv"
    csv_top10 = out_dir / f"pipeline_{stamp}_top10.csv"
    csv_detail = out_dir / f"pipeline_{stamp}_detail.csv"

    full.to_csv(csv_full, index=False, encoding="utf-8-sig")
    top10.to_csv(csv_top10, index=False, encoding="utf-8-sig")
    details.to_csv(csv_detail, index=False, encoding="utf-8-sig")

    with open(md_path, "w", encoding="utf-8") as f:
        today = datetime.now().strftime("%Y-%m-%d")
        cols = ["ticker", "name", "sector", "total", "eps_2026", "eps_2027",
                "rev_1m", "rev_3m", "foreign_20d", "main_force_20d",
                "dist_60d_high", "ma20", "ma60", "close",
                "entry_low", "entry_high", "stop_loss", "target_price", "rr"]

        def table(df, title):
            f.write(f"\n## {title}\n\n")
            use = [c for c in FULL_COLUMNS if c in df.columns]
            f.write(df[use].to_markdown(index=False))
            f.write("\n")

        f.write(f"# 找買點量化表 {today}\n")

        # 一、Top5 買點表（若提供）
        if top5 is not None and not top5.empty:
            use5 = ["ticker", "name", "sector", "grade", "total",
                    "eps_2026", "eps_2027", "rev_1m", "rev_3m",
                    "dist_60d_high", "close",
                    "entry_low", "entry_high", "stop_loss",
                    "target_price", "rr", "conclusion"]
            use5 = [c for c in use5 if c in top5.columns]
            f.write("\n## 一、Top5 買點清單\n\n")
            f.write(top5[use5].to_markdown(index=False))
            f.write("\n")

        table(top10, "二、表二：Top10 量化表")
        table(full, "三、表一：全量量化表（附錄）")

        # 四、淘汰名單
        f.write("\n## 四、淘汰名單（含規則編號）\n\n")
        if rejected is not None and not rejected.empty:
            ure = [c for c in ("ticker", "name", "rejected_rules", "total")
                   if c in rejected.columns]
            f.write(rejected[ure].to_markdown(index=False))
            f.write("\n")
        else:
            f.write("無淘汰標的。\n")

        # 五、資料源統計
        st = stats or {}
        f.write("\n## 五、資料源統計\n\n")
        f.write(f"- 主路徑成功：{st.get('primary', 0)} 次\n")
        f.write(f"- FinMind 備援啟用：{st.get('finmind', 0)} 次\n")
        f.write(f"- 備援仍失敗（N/A）：{st.get('failures', 0)} 次\n")

    # 六、欄位計算說明（稽核附錄——獨立章節，不與表格混排）
    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n## 六、欄位計算說明（稽核附錄）\n")
        f.write("\n### 6-A 公式總表\n\n")
        f.write("| 欄位 | 公式 | 資料源 |\n|---|---|---|\n")
        for col, formula, src in FORMULA_TABLE:
            f.write(f"| {col} | {formula} | {src} |\n")
        f.write("\n### 6-B 每檔計算數值（欄位順序與 6-A 對應，原始數值＋子項得分）\n\n")
        if details is not None and not details.empty:
            spec_cols = [c for c, _, _ in FORMULA_TABLE if c in details.columns]
            other = [c for c in details.columns if c not in spec_cols
                     and c != "ticker"]
            f.write(details[["ticker"] + spec_cols + other].to_markdown(
                index=False))
            f.write("\n")
        else:
            f.write("無明細。\n")

    logger.info("報表輸出：%s（+%s / %s / %s）",
                md_path.name, csv_full.name, csv_top10.name, csv_detail.name)
    return md_path, csv_full, csv_top10, csv_detail
