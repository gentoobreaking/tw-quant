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
def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("etf_candidates", "universe_ttl_days", "min_pool_size",
                "rr_thresholds", "rate_limit"):
        if key not in cfg:
            raise KeyError(f"config_pipeline.json 缺少必要鍵: {key}")
    return cfg


def make_rate_limiter(cfg: dict):
    from common.rate_limit import RateLimiter
    rl_cfg = {name: {"delay": v["delay"], "jitter": v.get("jitter", 0.0)}
              for name, v in cfg["rate_limit"].items()}
    return RateLimiter(rl_cfg)


# ---------------------------------------------------------------- stages
def stage0_universe(cfg: dict, cache: DiskCache,
                    rebuild: bool = False) -> pd.DataFrame:
    """Stage 0：股票池建構（實作於 T006/T007）

    回傳欄位固定為 UNIVERSE_COLUMNS。
    """
    if not rebuild and UNIVERSE_PATH.exists():
        df = pd.read_csv(UNIVERSE_PATH, dtype={"ticker": str})
        missing = set(UNIVERSE_COLUMNS) - set(df.columns)
        if not missing:
            logger.info("載入既有 universe.csv：%d 檔（%d 天內有效，"
                        "ttl=%d 天）", len(df), cfg["universe_ttl_days"],
                        cfg["universe_ttl_days"])
            return df[UNIVERSE_COLUMNS]
        logger.warning("universe.csv 欄位缺 %s，將重建", missing)

    # T006/T007 實作點：ETF 排名 → 成分股去重 → MoneyDJ 族群標記
    raise NotImplementedError(
        "stage0 建構邏輯尚未實作（T006/T007）。"
        "可先以 --rebuild-universe=false 搭配既有 universe.csv 執行。")


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

    universe = stage0_universe(cfg, cache, rebuild=args.rebuild_universe)
    logger.info("stage0 完成：股票池 %d 檔", len(universe))

    scored = stage1_scoring(universe, cfg, cache)
    logger.info("stage1 完成：%d 檔已評分", len(scored))

    final = stage2_grading(scored, cfg)
    logger.info("stage2 完成：%d 檔進入最終清單", len(final))

    cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
