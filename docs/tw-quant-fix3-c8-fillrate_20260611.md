# TW-Quant 修改 3：C8 高股息填息自動化

**日期**: 2026-06-11
**狀態**: ✅ 完成

## 修改內容

將 C8（高股息填息檢查）從 `manual: True` 佔位符升級為自動化檢查。利用 yfinance dividend history + unadjusted close 計算近 3 次除息後的填息狀態。

## 改動檔案

| 檔案 | 改動 |
|------|------|
| `etf_screener.py` | `check_c8` 函式完全重寫：新增 `check_c8(type_label, ticker, df)` 自動填息計算 |
| `etf_screener.py` | `screen_one` + quick_mode 兩處 `check_c8` 呼叫加入 `ticker, df` 參數 |
| `etf_screener.py` | `_DEFAULTS` 加入 C8 config：`fill_days_max=30, divs_to_check=3, min_fill_ratio=0.6` |
| `config_etf.json` | 新增 C8 區塊 |

## 填息邏輯

1. yfinance 取 unadjusted close（非 auto_adjust），避免調整後價格掩蓋除息跳空
2. 取近 3 次除息日期（`yf.Ticker.dividends`）
3. 對每次除息：前一日 unadjusted close 為填息目標，往後 30 個交易日檢查是否站回
4. 3 次中至少 2 次填息（min_fill_ratio=0.6）→ C8=合格
5. 失敗時 fallback 到 `manual: True`

## 關鍵設計決策

- **min_fill_ratio=0.6**（非 0.67）：2/3=0.6667 < 0.67，精度問題會導致 2/3 填息被判不合格
- **unadjusted close**：auto_adjust=True 會把除息影響分攤到歷史價格，導致「跳空」不明顯
- **獨立 yf.download**：不依賴 screen_one 的 df（那個是 auto_adjust=True），C8 函式內部另取 unadjusted

## 測試結果

- pytest 43/43 全通 ✅
- 0056: 填息2/3 (10/23❌ 01/22✅2d 04/23✅7d) → ok=True ✅
- 00878: 填息2/3 (11/18✅12d 02/26❌ 05/19✅3d) → ok=True ✅
- 0050 (市值型): 不適用 → ok=True ✅
- 99999 (不存在): fallback manual=True ✅
- 槓桿型: 不適用 ✅
