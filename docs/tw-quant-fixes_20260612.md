# TW-Quant 第二輪審查修復紀錄

**日期**: 2026-06-12
**修復範圍**: 12 項問題（3 高 + 4 中 + 5 低），依序實作

## 已完成修復

### 🔴 高優先級

1. **C16 gain_max 30%→80%** — 與 README 統一，避免強勢股被硬淘汰
   - `stock_screener.py` _DEFAULTS: 30.0→80.0
   - `config.json`: 30.0→80.0
   - `etf_screener.py` _DEFAULTS: 市值型 20.0→80.0, 高股息 15.0→40.0
   - `config_etf.json`: 同上

2. **C17 加入 hard_reject** — 高檔跌破月線直接淘汰
   - `config.json` / `config_etf.json` / `_DEFAULTS` (兩個 screener) / `DEFAULT_HARD_REJECT_RULES` (scoring.py): 新增 `"c17": false`
   - `_build_result` conditions dict: 新增 `"c17": pos.get("c17", True)`
   - `print_summary` label_map: 新增 `"c17": "C17 非高檔破月線"`
   - README Hard Reject 表: 新增 C17 列

3. **ETF manual 旗標偏誤修正** — manual=True 時假定通過（保留警告）
   - `etf_screener.py` _build_result: `c6.get("ok", False) and not c6_manual` → `c6.get("ok", False) or c6_manual`
   - C6/C7/C8 同理修正

### 🟡 中優先級

4. **C10 回退邏輯修正** — 無 sigma 資料時預設 False
   - `stock_screener.py` check_fund: `c10 = c9` → `c10 = False`

5. **C8 填息檢查快取化** — 避免重複下載
   - `etf_screener.py` check_c8: dividends 和 price data 透過 cache.get() 快取 86400s

6. **E2 變數重命名 + 訊號描述修正**
   - `common/scoring.py`: `high_20d` → `prior_high`, 註解更新, 訊號文字改為「高位死叉」

7. **print_summary label_map 動態化**
   - C5/C6/C7 標籤從固定門檻改為「分組門檻」

### 🟢 低優先級

8. **ENTER 門檻設計意圖加註** — `common/scoring.py` classify_tier 加註解
9. **殖利率 sanity cutoff 50%→15%** — `etf_screener.py` classify_etf
10. **Quick mode 不對稱加註** — `etf_screener.py` 加註解說明
11. **C5 eps_min 死代碼標註** — `_DEFAULTS` 加 `# 已由 market_cap_groups 覆蓋`
12. **C14 proximity_max 加註** — `stock_screener.py` 說明 5% 上限的設計意圖

## 測試結果
- 43/43 tests passed ✅

## 修改檔案清單
- `stock_screener.py`
- `etf_screener.py`
- `common/scoring.py`
- `config.json`
- `config_etf.json`
- `README.md`
