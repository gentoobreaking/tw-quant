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
            "close": mom["close"], "ma20": mom["ma20"], "ma60": mom["ma60"],
            "dist_60d_high": mom["dist_60d_high"], "rsi14": mom["rsi14"],
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
                  details: pd.DataFrame, out_dir: Path = RESULT_DIR):
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
            missing = [c for c in cols if c not in df.columns]
            if missing:
                f.write(f"> 缺欄位：{missing}\n\n")
            f.write(df[use].to_markdown(index=False))
            f.write("\n")

        f.write(f"# 找買點量化表 {today}\n")
        table(top10, "表二：Top10 量化表")
        table(full, "表一：全量量化表（附錄）")

    logger.info("報表輸出：%s（+%s / %s / %s）",
                md_path.name, csv_full.name, csv_top10.name, csv_detail.name)
    return md_path, csv_full, csv_top10, csv_detail
