# TW-Quant 篩選系統全面審查報告

> 審查日期：2026-06-10
> 審查範圍：stock_screener.py, etf_screener.py, README.md, README_ETF.md, improvement.md, logic_improve.md

## 審查結論

improvement.md 和 logic_improve.md 已經做了非常詳盡的工程面和邏輯面審查，以下僅補充它們未涵蓋的問題。

---

## 🔴 P0：必須立即修復

### 1. C11 籌碼對齊 bug（最嚴重的邏輯 bug）

**位置**：兩個 screener 的 `get_chip()` + `check_chip()`

**問題**：`get_chip` 只取近 6 個交易日的法人買賣超，但 `check_chip` 用 `below_ma` 的 list 長度推算 `fn` 的起始位置（`idx_start = max(0, len(fn) - len(below_ma) - 5)`），日期完全沒有對齊。

當股價在 5/1-5/5 跌破 20MA，但 fn 只有 5/20-5/27 的法人資料時，程式以為 fn_seg 對應跌破期間，實際上根本對不上。

**修法**：在 get_chip 中保留日期資訊，在 check_chip 中按日期 join。

**時程**：1 小時

### 2. 位階重新設計：C14/C15 對齊「60MA 回測買點」

**問題**：現行 C14（跌幅≥30%）和 C15（橫盤≥90日）是股災抄底邏輯，與 C1-C4（上升趨勢）硬用 AND 連接，幾乎不可能同時通過。使用者的實際交易策略是「60MA 回測買點」——找穩定但跌到接近 60MA 的股票，剛開始往上漲。

**修法**：見 logic_improve.md §15 的 C14_new（距60MA 0-5%）和 C15_new（連3日上漲）。

**時程**：半天

---

## 🟡 P1：應盡快修復

### 3. C8 冗餘移除

C6（負債比<50%）⊆ C8（負債比<60%），AND 鏈中 C8 貢獻為零。

**時程**：15 分鐘

### 4. C3 lookback_days 2→5

前 1-2 日最低價曾跌破 20MA 太短，週一等級的回測幾乎抓不到。

**時程**：5 分鐘

### 5. ETF C5 溢價門檻放寬 + 幣別處理 + 折價加分

- 0.5% 門檻過嚴（00878 除息前常超 1%）
- 跨境 ETF 幣別未處理
- 折價（負溢價）是套利機會，應加分而非僅通過

**時程**：1 小時

---

## 🟡 P2：重要改善

### 6. 共用模組抽取

兩個 screener 有 ~60% 複製貼上（~575 行重複）。抽取為 common/ 套件是後續所有改動的基礎。

**時程**：1 天

### 7. KD warmup + 60 分線 C18 抽出

- KD(20,5,5) 需約 20 根 K 棒 warmup，60 分線前 20 根失真
- C18 的 60 分線下載不應放在 check_position 函數中

**時程**：2 小時

### 8. 得分制 + 三層輸出（ENTER/WATCH/EXIT）

見 logic_improve.md §10 + §18。

**時程**：1 天

---

## 🟢 P3：改善項目

### 9. C16 門檻合理化（個股 80%→30%，ETF 配合調整）
### 10. ETF C6 改為按權重加權（至少查前 10 大持股）
### 11. Logging + 結果持久化
### 12. TDCC 頁面變更偵測
### 13. ETF classify_etf 的 dividendYield 格式判斷不穩健
### 14. ETF screen_batch 的 quick mode 不應跳過基本面（C5-C8 成本很低）

---

## 附錄：快取系統隱含 bug

1. **lambda 閉包陷阱**：`_disk_cache(ck, lambda: _df_to_dict(df))` 中 df 是閉包引用，若延遲執行會拿到錯誤值
2. **單一 JSON 檔案無上限**：1116 檔股票快取可超 80MB，json.load() 首開要 3-5 秒，建議改 SQLite
3. **`_DISK_CACHE_LOADED` 全域 flag**：多執緒下不安全（目前單執緒無礙，但未來加速時會踩坑）
