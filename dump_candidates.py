#!/usr/bin/env python3
"""
dump_candidates.py — TWSE 公開 API 候選清單產生器
====================================================
資料來源：
  • ETF  → TWSE JSON API: https://www.twse.com.tw/rwd/zh/ETF/list
  • ETN  → TWSE JSON API: https://www.twse.com.tw/rwd/zh/ETN/list
  • REAT → twstock.codes 分類（TWSE 無 REAT JSON API）
  • 股票 → TWSE 財報 API (t187ap14_L) 全部公司代號，去除前三類

輸出（覆寫）：
  candidates.csv      — 普通股候選（上市+上櫃）
  candidates_ETF.csv — ETF（TWSE API，含名稱欄）
  candidates_ETN.csv — ETN（TWSE API，含名稱欄）
  candidates_REAT.csv — 不動產資產證券化（twstock 分類，含名稱欄）

使用方式：
  python3 dump_candidates.py [--check]
  --check : 只比對差異，不寫入（dry-run）
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional
from datetime import datetime

import requests
import twstock

BASE_DIR = Path(__file__).parent
HEADERS  = {"User-Agent": "Mozilla/5.0"}
TIMEOUT  = 15
MAX_RETRIES = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dump_candidates")


# ──── 低層 request ────

def fetch_json(url: str, retries: int = MAX_RETRIES) -> Optional[dict]:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            log.warning("HTTP %d from %s", r.status_code, url)
        except Exception as e:
            log.warning("Attempt %d/%d failed for %s: %s", attempt + 1, retries, url, e)
        if attempt < retries - 1:
            time.sleep(1.5 ** attempt)
    return None


def fetch_twse_list(path: str) -> List[list]:
    url = f"https://www.twse.com.tw{path}"
    data = fetch_json(url)
    if data and "data" in data:
        return data["data"]
    return []


# ──── 抓 TWSE API ────

def get_etf_list() -> Dict[str, str]:
    """從 TWSE ETF 清單 API 取得 {ticker: 名稱}

    注意：部分 ETF 有多幣別報價（用 <br> 分隔，如 006205(新臺幣)<br>00625K(人民幣)），
    以及槓桿/反向 ETF（代號結尾 L/R，如 00631L, 00632R）。
    解析時需先拆分 <br>，再以英數字元過濾。
    """
    data = fetch_twse_list("/rwd/zh/ETF/list")
    if not data:
        log.error("無法取得 ETF 清單（TWSE API 無回應）")
        return {}
    result = {}
    for row in data:
        if len(row) < 3:
            continue
        # 名稱欄（去 <br>）
        raw_name = str(row[2]).strip()
        name = raw_name.split("<br>")[0].strip()
        # 代號欄可能含多組（以 <br> 分隔）
        raw_tickers = str(row[1]).strip()
        for raw_t in raw_tickers.split("<br>"):
            # 移除幣別標記：括號及其內容 (新臺幣) → 取括號前
            ticker = raw_t.strip()
            # 去掉尾部 (幣別) 標記，如 "00625K(人民幣)" → "00625K"
            ticker = ticker.split("(")[0].strip()
            # 接受 4-6 碼英數（正則）
            if not ticker.isalnum() or len(ticker) < 4 or len(ticker) > 6:
                continue
            result[ticker] = name
    log.info("TWSE ETF: %d 檔（已處理多幣別及 L/R）", len(result))
    return result


def get_etn_list() -> Dict[str, str]:
    """從 TWSE ETN 清單 API 取得 {ticker: 名稱}"""
    data = fetch_twse_list("/rwd/zh/ETN/list")
    if not data:
        log.error("無法取得 ETN 清單（TWSE API 無回應）")
        return {}
    result = {}
    for row in data:
        if len(row) < 3:
            continue
        raw_name = str(row[2]).strip()
        name = raw_name.split("<br>")[0].strip()
        raw_tickers = str(row[1]).strip()
        for raw_t in raw_tickers.split("<br>"):
            ticker = raw_t.split("(")[0].strip()
            if not ticker.isalnum() or len(ticker) < 4 or len(ticker) > 6:
                continue
            result[ticker] = name
    log.info("TWSE ETN: %d 檔", len(result))
    return result


def get_reat_list() -> Dict[str, str]:
    """從 twstock.codes 分離「受益證券-不動產投資」（REAT）

    TWSE 無 REAT JSON API，需靠 twstock 分類。
    需與「受益證券-資產基礎證券」（ABS/資產抵押證券）區分。
    正確 REAT type 含有「受益證券」+「不動產」；ABS 只有「受益證券」+「資產」。
    """
    result = {}
    for ticker, info in twstock.codes.items():
        if info.market not in ("上市", "上櫃"):
            continue
        t = info.type or ""
        # 「受益證券」+「不動產」= REAT；排除只有「資產」的 ABS
        if "受益證券" in t and "不動產" in t:
            result[ticker] = info.name
    log.info("twstock REAT (不動產投資): %d 檔", len(result))
    return result


def get_stock_list() -> Dict[str, str]:
    """從 TWSE t187ap14_L 取得 {ticker: 公司名稱}"""
    data = fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap14_L")
    if not data:
        log.error("無法取得上市公司清單（t187ap14_L 無回應）")
        return {}
    result = {}
    for row in data:
        code = row.get("公司代號", "").strip()
        name = row.get("公司名稱", "").strip()
        if code.isdigit() and len(code) >= 4 and name:
            result[code] = name
    log.info("TWSE 上市公司 (t187ap14_L): %d 檔", len(result))
    return result


# ──── 輸出 ────

def write_ticker_csv(path: Path, tickers: List[str], extra: Optional[dict] = None):
    """寫入候選 CSV"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker"] + (["name"] if extra else []))
        for t in sorted(tickers):
            row = [t]
            if extra:
                row.append(extra.get(t, ""))
            w.writerow(row)


def diff_report(
    etf_d: dict, etn_d: dict, reat_d: dict,
    etf_old: set, etn_old: set, reat_old: set,
):
    log.info("═══ 新舊比對 ═══")
    for label, new_s, old_s in [
        ("ETF",  set(etf_d.keys()),  etf_old),
        ("ETN",  set(etn_d.keys()),  etn_old),
        ("REAT", set(reat_d.keys()), reat_old),
    ]:
        new_only = new_s - old_s
        old_only = old_s - new_s
        if new_only:
            log.info("  %s 新增: %s", label, sorted(new_only))
        if old_only:
            log.info("  %s 移除: %s", label, sorted(old_only))
        if not new_only and not old_only:
            log.info("  %s 無變動", label)


# ──── 主程式 ────

def main(check_only: bool = False):
    log.info("TW-Quant 候選清單產生器")
    log.info("時間: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("")

    # 1. TWSE API
    log.info("── 抓取 TWSE API ──")
    etf_d  = get_etf_list()
    etn_d  = get_etn_list()
    reat_d = get_reat_list()

    # 2. 上市公司完整清單
    log.info("── 抓取上市公司清單 ──")
    all_twse = get_stock_list()

    # 3. 股票池（排除 ETF/ETN/REAT）
    excluded = set(etf_d) | set(etn_d) | set(reat_d)
    stock_dict = {k: v for k, v in all_twse.items() if k not in excluded}
    stock_tickers = sorted(stock_dict)

    log.info("")
    log.info("── 統計 ──")
    log.info("  ETF  : %4d 檔", len(etf_d))
    log.info("  ETN  : %4d 檔", len(etn_d))
    log.info("  REAT : %4d 檔", len(reat_d))
    log.info("  股票 : %4d 檔（%d 上市公司 − %d 排除）",
             len(stock_dict), len(all_twse), len(excluded))
    total = len(etf_d) + len(etn_d) + len(reat_d) + len(stock_dict)
    log.info("  合計 : %4d 檔", total)

    # 4. 讀舊檔比對
    def read_old(path: Path) -> Set[str]:
        if not path.exists():
            return set()
        with open(path) as f:
            reader = csv.DictReader(f)
            return {row.get("ticker", "").strip()
                    for row in reader if row.get("ticker", "").strip()}

    old_etf   = read_old(BASE_DIR / "candidates_ETF.csv")
    old_etn   = read_old(BASE_DIR / "candidates_ETN.csv")
    old_reat  = read_old(BASE_DIR / "candidates_REAT.csv")
    old_stock = read_old(BASE_DIR / "candidates.csv")

    if old_etf or old_etn or old_reat:
        diff_report(etf_d, etn_d, reat_d, old_etf, old_etn, old_reat)

    if check_only:
        log.info("[DRY-RUN] 未寫入檔案")
        return

    # 5. 寫入
    log.info("")
    log.info("── 寫入檔案 ──")

    def write_csv(path: Path, tickers: list, extra: Optional[dict] = None):
        write_ticker_csv(path, tickers, extra)
        log.info("  %s: %d 檔 -> %s", path.name, len(tickers), path)

    write_csv(BASE_DIR / "candidates_ETF.csv",   list(etf_d.keys()),   etf_d)
    write_csv(BASE_DIR / "candidates_ETN.csv",    list(etn_d.keys()),   etn_d)
    write_csv(BASE_DIR / "candidates_REAT.csv",   list(reat_d.keys()),  reat_d)
    write_csv(BASE_DIR / "candidates.csv",         stock_tickers,        stock_dict)

    log.info("")
    log.info("完成！")
    log.info("  candidates.csv      : %d 檔", len(stock_dict))
    log.info("  candidates_ETF.csv : %d 檔", len(etf_d))
    log.info("  candidates_ETN.csv : %d 檔", len(etn_d))
    log.info("  candidates_REAT.csv: %d 檔", len(reat_d))
    log.info("  總計               : %d 檔", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TW-Quant 候選清單產生器")
    parser.add_argument("--check", action="store_true",
                        help="只比對差異，不寫入")
    args = parser.parse_args()
    try:
        main(check_only=args.check)
    except KeyboardInterrupt:
        log.info("中斷")
        sys.exit(1)