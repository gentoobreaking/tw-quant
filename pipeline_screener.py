#!/usr/bin/env python3
"""pipeline_screener.py — 找買點量化篩選管線（T003）

三段式漏斗：
  Stage 0  股票池建構（ETF Top5 成分股 → MoneyDJ 族群標記）→ data/universe.csv
  Stage 1  100 分量化評分 → 表一（50 檔全量）/ 表二（Top10）
  Stage 2  硬淘汰 + S/A/B 訊號分級 → Top5

共用模組: common/ (cache, rate_limit, yf_utils, twse, tdcc, finmind)
規格書:   ~/tasks/tw-quant/spec.md
任務書:   ~/tasks/tw-quant/tasks/T004–T014
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from common.cache import DiskCache
from common.logger import setup_logger

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config_pipeline.json"
UNIVERSE_PATH = BASE_DIR / "data" / "universe.csv"
CACHE_PATH = BASE_DIR / ".cache" / "tw_quant.db"

# universe.csv schema（T004 定義，T007 產生）
UNIVERSE_COLUMNS = ["ticker", "name", "sector", "etf_sources", "count"]

logger = setup_logger("pipeline")


# ---------------------------------------------------------------- config
SECRETS_PATH = BASE_DIR / "config_secrets.json"


def _load_secrets(cfg: dict) -> dict:
    """機密一律走環境變數（避免進版控/設定檔）：

    - FINMIND_TOKEN：FinMind 備援通道
    - OPENAI_API_KEY / OPENAI_BASE_URL：AI 評估端點
      （OPENAI_* 由 common.ai_review.resolve_ai_config 直接讀取）
    config_secrets.json 為相容舊制的可選來源。
    """
    import os
    secrets_path = Path(SECRETS_PATH)
    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            secrets = json.load(f)
        for k, v in secrets.items():
            if isinstance(v, dict):   # 如 ai_review.api_key 巢狀
                cfg.setdefault(k, {}).update(v)
            else:
                cfg[k] = v
    # 環境變數最高優先
    if os.environ.get("FINMIND_TOKEN"):
        cfg["finmind_token"] = os.environ["FINMIND_TOKEN"]
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("etf_candidates", "universe_ttl_days", "min_pool_size",
                "rr_thresholds", "rate_limit"):
        if key not in cfg:
            raise KeyError(f"config_pipeline.json 缺少必要鍵: {key}")
    return _load_secrets(cfg)


def make_rate_limiter(cfg: dict):
    from common.rate_limit import RateLimiter
    # 管線用通道（config）＋共用既有通道（如 yahoo holdings 的 "yf"）
    merged = {
        "yf": {"delay": 0.5, "jitter": 0.2},
        **cfg["rate_limit"],
    }
    rl_cfg = {name: {"delay": v["delay"], "jitter": v.get("jitter", 0.0)}
              for name, v in merged.items()}
    return RateLimiter(rl_cfg)


# ---------------------------------------------------------------- stages
def stage0_universe(cfg: dict, cache: DiskCache, rate_limiter,
                    rebuild: bool = False) -> pd.DataFrame:
    """Stage 0：股票池建構（T006 排名去重＋T007 MoneyDJ 族群標記）

    回傳欄位固定為 UNIVERSE_COLUMNS。
    """
    if not rebuild and UNIVERSE_PATH.exists():
        df = pd.read_csv(UNIVERSE_PATH, dtype={"ticker": str})
        if not (set(UNIVERSE_COLUMNS) - set(df.columns)):
            logger.info("載入既有 universe.csv：%d 檔（TTL %d 天）",
                        len(df), cfg["universe_ttl_days"])
            return df[UNIVERSE_COLUMNS]
        logger.warning("universe.csv 欄位不完整，重建")

    from common.universe import build_pool, rank_etfs
    from common.etf_yahoo import fetch_top10_holdings
    from common.moneydj import build_sector_map
    from common.finmind import FinMindClient, with_fallback

    # Step A/B：ETF Top5 → 成分股去重（T006）
    top5 = rank_etfs(cfg)
    if not top5:
        raise RuntimeError("無法取得任何 ETF 價格資料，中止 Stage 0")
    pool = build_pool(top5,
                      holdings_fn=lambda t: fetch_top10_holdings(
                          t, cache, rate_limiter),
                      min_pool_size=int(cfg["min_pool_size"]))
    tickers = pool["ticker"].tolist()

    # Step C：族群標記（T007）——MoneyDJ 主路徑
    sector_map = build_sector_map(tickers, rate_limiter, cache)

    # 補源：FinMind TaiwanStockInfo（一次取全部名稱與官方產業別）
    finmind = FinMindClient(token=cfg.get("finmind_token") or None,
                            rate_limiter=rate_limiter)
    info_rows, source = with_fallback(
        lambda: [],
        lambda: finmind.fetch_dataset("TaiwanStockInfo"),
        label="TaiwanStockInfo")
    name_by_id = {str(r.get("stock_id")): r.get("stock_name", "")
                  for r in info_rows}
    industry_by_id = {str(r.get("stock_id")): r.get("industry_category", "")
                      for r in info_rows}
    if source == "finmind":
        for t in tickers:
            sector_map.setdefault(t, industry_by_id.get(t, ""))

    pool.insert(1, "name", [name_by_id.get(t, "") for t in pool["ticker"]])
    pool.insert(2, "sector", [sector_map.get(t, "UNKNOWN")
                              or industry_by_id.get(t, "UNKNOWN")
                              for t in pool["ticker"]])
    result = pool[[c for c in UNIVERSE_COLUMNS]]

    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(UNIVERSE_PATH, index=False, encoding="utf-8")
    unknown = int((result["sector"] == "UNKNOWN").sum())
    logger.info("universe.csv 寫入：%d 檔（sector UNKNOWN %d 檔）",
                len(result), unknown)
    return result


def stage1_scoring(universe: pd.DataFrame, cfg: dict,
                   cache: DiskCache) -> pd.DataFrame:
    """Stage 1：100 分量化評分（實作於 T008–T011）"""
    if universe.empty:
        logger.warning("stage1：股票池為空，跳過")
        return pd.DataFrame()
    # T011 實作點：因子①–⑤ → total → 表一(全量)/表二(Top10)
    raise NotImplementedError("stage1 評分邏輯尚未實作（T008–T011）")


def stage2_grading(scored: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Stage 2：硬淘汰＋S/A/B 分級（實作於 T012/T013）"""
    if scored.empty:
        logger.warning("stage2：無候選股，跳過")
        return pd.DataFrame()
    # T012/T013 實作點：硬淘汰 → 進場區/停損/目標價/R/R → S/A/B
    raise NotImplementedError("stage2 分級邏輯尚未實作（T012/T013）")


# ---------------------------------------------------------------- main
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="找買點量化篩選管線")
    parser.add_argument("--rebuild-universe", action="store_true",
                        help="強制重建 data/universe.csv（忽略 TTL）")
    parser.add_argument("--top", type=int, default=10,
                        help="量化表保留前 N 名（預設 10）")
    parser.add_argument("--dry-run", action="store_true",
                        help="僅驗證設定與骨架，不發任何網路請求")
    args = parser.parse_args(argv)

    cfg = load_config()
    logger.info("找買點管線啟動｜dry-run=%s｜top=%d",
                args.dry_run, args.top)

    if args.dry_run:
        # 不建立任何需要網路的物件；快取僅本地 SQLite
        cache = DiskCache(str(CACHE_PATH), ttl=7200)
        empty = pd.DataFrame(columns=UNIVERSE_COLUMNS)
        logger.info("[dry-run] stage0_universe -> shape %s", (0, len(UNIVERSE_COLUMNS)))
        logger.info("[dry-run] stage1_scoring   -> shape %s", empty.shape)
        logger.info("[dry-run] stage2_grading   -> shape %s", empty.shape)
        logger.info("[dry-run] etf_candidates=%d 檔｜min_pool_size=%d｜rr_thresholds=%s",
                    len(cfg["etf_candidates"]), cfg["min_pool_size"],
                    cfg["rr_thresholds"])
        cache.close()
        logger.info("[dry-run] 完成，未發出任何網路請求")
        return 0

    rate_limiter = make_rate_limiter(cfg)
    cache = DiskCache(str(CACHE_PATH), ttl=7200)

    from common.finmind import FinMindClient, get_stats, reset_stats
    from common.grading import annotate_signals, build_top5
    from common.scorer import run_scoring, write_reports

    reset_stats()
    finmind = FinMindClient(token=cfg.get("finmind_token") or None,
                            rate_limiter=rate_limiter)

    try:
        logger.info("── Stage 0：股票池建構 ──")
        universe = stage0_universe(cfg, cache, rate_limiter,
                                   rebuild=args.rebuild_universe)
        logger.info("stage0 完成：股票池 %d 檔", len(universe))

        logger.info("── Stage 1：100 分量化評分 ──")
        full, top10, details = run_scoring(
            universe, cfg, cache,
            rate_limiter=rate_limiter, finmind=finmind)
        logger.info("stage1 完成：%d 檔已評分", len(full))

        logger.info("── Stage 2：硬淘汰＋S/A/B 分級（全量標註） ──")
        annotated, graded_passed = annotate_signals(full, cfg["rr_thresholds"])
        rejected = annotated[annotated["grade"] == "R"]
        top5 = build_top5(graded_passed, top_n=5)
        top10 = annotated[annotated["grade"] != "R"].head(args.top)
        logger.info("stage2 完成：%d 檔淘汰 / %d 檔分級 / Top%d 輸出",
                    len(rejected), len(graded_passed), len(top10))

        # AI 質性覆核（選配；未啟用時欄位為「—」）
        if not args.dry_run:
            from common.ai_review import ai_evaluate
            ai_cfg = cfg.get("ai_review", {})
            reviews = [ai_evaluate(r.to_dict(), ai_cfg, cache=cache)
                       for _, r in top5.iterrows()]
            top5["AI評估"] = reviews
            logger.info("AI 覆核完成：%d 檔", len(reviews))

        md_path, *_ = write_reports(annotated, top10, details,
                                    rejected=rejected,
                                    stats=get_stats(), top5=top5)
        logger.info("完成！報表：%s", md_path)
    except Exception as e:  # noqa: BLE001
        logger.error("管線執行失敗：%s", e)
        cache.close()
        return 1

    finmind.close()
    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
