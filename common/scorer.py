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
        # 稽核明細：子項層級
        detail = {"ticker": t}
        for k, v in {**fund["_sub"], **{f"f2_{k2}": v2 for k2, v2 in eps["_sub"].items()},
                     **{f"f3_{k}": v for k, v in chips["_sub"].items()},
                     **{f"f4_{k}": int(v) if isinstance(v, bool) else v
                        for k, v in mom["_sub"].items()},
                     **{f"f5_{k}": int(v) if isinstance(v, bool) else v
                        for k, v in pos["_sub"].items()}}.items():
            detail[k] = v
        detail.update({"total": total})
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
        f.write("| total | f1+f2+f3+f4+f5（上限100） | — |\n")
        f.write("| f1 因子①(25) | EPS成長10＋營收YoY均6＋ROE3＋毛利率趨勢2＋FCF4 | yfinance/TWSE/FinMind |\n")
        f.write("| f2 因子②(30) | 1M上修12＋3M上修8＋revisions動能6＋相對產業4 | yfinance eps_trend/revisions/growth_estimates |\n")
        f.write("| rev_1m | (current−30daysAgo)/abs(30daysAgo)×100 | yfinance eps_trend |\n")
        f.write("| rev_3m | (current−90daysAgo)/abs(90daysAgo)×100 | 同上 |\n")
        f.write("| f3 因子③(20) | 外資5日6+外資20日4+投信5日4+投信20日2+同向2+外資持股變化2 | TWSE fund/T86／FinMind 備援 |\n")
        f.write("| main_force_20d | 近20日投信+外資合計淨買超張數 | 同上 |\n")
        f.write("| f4 因子④(15) | >20MA3+MA20上彎3+金叉/排列3+量能3+相對大盤3 | 日線（未還原） |\n")
        f.write("| dist_60d_high | (close/max60日高−1)×100 | 日線 |\n")
        f.write("| f5 因子⑤(10) | 距60日高回撤≥5%→3+距120日高≥8%→3+52W下半部2+止跌確認2 | 日線 |\n")
        f.write("| entry_low/high | [20MA, 20MA×1.03]；資格：現價>60MA>條件見 algs/entry-stop-target.md | 日線 MA |\n")
        f.write("| stop_loss | min(中值×0.93, 發動K最低×0.995)；邏輯停損=破60MA+KD死叉 | 日線 |\n")
        f.write("| target_price | min(近60日高, 分析師mean) 取離現價較近者；<8% 改較遠者 | yfinance price_targets |\n")
        f.write("| rr | (目標價−中值)/(中值−停損)，門檻 S=2.0/A=1.5 | 衍生 |\n")
        f.write("\n### 6-B 每檔計算數值（子項層級）\n\n")
        if details is not None and not details.empty:
            f.write(details.to_markdown(index=False))
            f.write("\n")
        else:
            f.write("無明細。\n")

    logger.info("報表輸出：%s（+%s / %s / %s）",
                md_path.name, csv_full.name, csv_top10.name, csv_detail.name)
    return md_path, csv_full, csv_top10, csv_detail
