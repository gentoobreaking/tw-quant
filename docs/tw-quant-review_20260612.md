# TW-Quant 邏輯判斷審查報告 (第二輪)

**日期**: 2026-06-12  
**審查範圍**: stock_screener.py (1148行) + etf_screener.py (1325行)  
**審查重點**: 評分邏輯、條件權重、硬淘汰、出場信號、快速模式、ETF 專屬邏輯

---

## 🔴 高優先級 (影響評分/分類精準度)

### 1. C16 gain_max 30% vs README 宣稱 80% — 硬淘汰邏輯矛盾

**位置**: `config.json` conditions.C16.gain_max = 30.0, `_DEFAULTS` 同  
**問題**: README 寫「近40日未漲≥80%（未翻倍）」，但 config 實際設 30%。這導致任何近 2 個月漲幅超過 30% 的股票被硬淘汰（`hard_reject_rules.c16=false` → c16==false 時淘汰）。

**影響**: 極大。多頭行情中的強勢股（如 2330 波段 40%）會被系統直接丟入 OUT。
*   若原本設計目標就是 30%（保守進場），這合理但應修正 README。
*   若 README 對（80%），則應改 config 為 80%。

**修改**: 確認設計意圖後，二選一修正。

---

### 2. C17 孤兒條件 — 計算但對評分/分類零影響

**位置**: 兩個 screener 的 `check_position`  
**問題**: C17 (高檔震盪跌破月線) 被計算並回傳 `c17: not high_breakdown`，但：
- 不在 `scoring_weights` 中（不計分）
- 不在 `hard_reject_rules` 中（不觸發淘汰）
- 在 `_build_result` 的 conditions dict 也不存在

因此 C17 完全無作用，但每檔股票都在算它。

**影響**: 浪費計算但無實質損害。真正的問題是：高檔跌破月線確實是危險訊號，應該發揮作用。

**修改方案 (二選一)**:
- **方案 A（權重）**: 加入 scoring_weights，給 5-10 分，失敗時扣分
- **方案 B（硬淘汰）**: 加入 hard_reject_rules = `{"c17": false}`，觸發直接 OUT

---

### 3. ETF manual 旗標 → 系統性偏誤（假定通過但計為失敗）

**位置**: `etf_screener.py` 的 `_build_result`  
**問題**: C6/C7/C8 在無資料時返回 `ok=True, manual=True`（假定通過），但在 scoring 中：
```python
"c6": c6.get("ok", False) and not c6_manual,
```
`manual=True` 時 `not c6_manual` = `False` → 條件強制失敗 → 扣 5 分。

這導致：
- 高股息 ETF 無可分配收益資料 → C7 永遠被扣 5 分
- 部分 ETF 無成分股資料 → C6 永遠被扣 5 分
- 填息計算失敗 → C8 永遠被扣 5 分

**影響**: 對高股息 ETF 產生 10~15 分的系統性偏誤，使其難以達到 ENTER (75分)。

**修改方案**:
```python
# 方案 A: manual → 保留 ok 值但不計分
"c6": c6.get("ok", False) if not c6_manual else False,
# 但這樣等於 0 分，仍算失敗

# 方案 B (推薦): manual → 視為通過 (給及格分）
"c6": True if c6_manual else c6.get("ok", False),
# 同時在 print_summary 提醒需手動確認
```

---

## 🟡 中優先級 (影響精準度但非致命)

### 4. C10 存貨檢查回退邏輯太寬鬆

**位置**: `stock_screener.py` `check_fund`  
**問題**: 當無法計算存貨週轉 std 時，`c10 = c9`（只要 C9 通過就 C10 通過）。這讓 C10 成為 C9 的冗餘條件，失去「存貨異常波動偵測」的原始目的。

**影響**: 停損能力減弱 — 存貨正在惡化但 std 樣本不足的股票能躲過 C10。

**修改**: C10 預設應為 `False`（資料不足 = 保守不給過），或至少降權。

---

### 5. C8 填息檢查：未快取的額外下載

**位置**: `etf_screener.py` `check_c8`  
**問題**: C8 額外呼叫 `yf.download(period="1y", auto_adjust=False)`，未經過快取層。這導致每次執行高股息 ETF 篩選都重複下載。

**影響**: 中等。延遲 + rate limit 佔用，但不影響正確性。

**修改**: 包裝進 cache.get()，或用 fetch_price 的已快取資料計算填息（unadjusted close 可從 yf.Ticker.history 取得）。

---

### 6. E2 出場信號邏輯：名稱誤導

**位置**: `common/scoring.py` `check_exit` E2  
**問題**: 
- 變數名 `high_20d` 暗示「20日高點」，實際是 lookback 前半段的最高價
- 觸發條件 `recent_high >= high_20d * 0.95 AND c20` 意為「價格仍在近期高點附近 + KD死叉」
- 這更像「高位死叉」，而非摘要描述的「高檔反轉」

**影響**: 低。但變數命名易造成未來維護誤解。

---

### 7. print_summary 標籤與實際門檻不一致

**位置**: `stock_screener.py` `print_summary` label_map  
**問題**: 
- 顯示「C5 EPS>0」→ 實際 large cap 門檻是 1.0, mid 是 0.5
- 顯示「C6 負債比<50%」→ 實際 large cap 門檻是 60%, small 是 45%
- 顯示「C7 營收YoY>10%」→ 實際 large 只需 5%, small 要 15%

**影響**: 使用者看到報告可能對自己的條件通過原因產生誤解。

**修改**: label_map 改用動態取值（從 market_cap_groups 取實際門檻）。

---

## 🟢 低優先級 (設計觀察 / 微優化)

### 8. ENTER 門檻 (c1+c14+c15) 偏向均值回歸策略

`classify_tier` 要求 ENTER 必須同時滿足 c1(>60MA)、c14(距60MA 0~5%)、c15(連3日漲)。這排除了「股價已高於 60MA 10% 但趨勢強勁」的股票（C14 失敗 → 最多 WATCH）。

**觀察**: 若這是設計意圖（只抓剛突破或回測完成的進場點），則 OK。但若希望捕捉趨勢延續的強勢股，可能需要放寬 C14 上限，或讓 C14 失敗時不影響 tier 判定。

---

### 9. classify_etf 股息率 sanity check 過高

`val > 0.5` 的 cutoff（即 50% 殖利率以上才當作壞資料）。yfinance 有時回傳 15-25% 的異常值，這些仍會通過。

**修改**: 降低 cutoff 到 0.15 (15%)。台股正常高股息殖利率在 3-8%，超過 15% 幾乎可確定是資料錯誤。

---

### 10. Quick mode 不對稱

- **個股 quick mode**: 跳過條件 = `NOT (tech.ok AND fund.ok)` → 技術+基本面都要過才繼續
- **ETF quick mode**: 跳過條件 = `NOT tech.ok` → 只看技術面

ETF quick mode 更寬鬆，可能讓基本面差的 ETF 進入完整掃描，但這對 ETF 來說可能是合理的（ETF 基本面的重要性低於個股）。

---

### 11. config.json C5 eps_min=0.0 是死代碼

`config.json` conditions.C5 設 `eps_min: 0.0`，但 `check_fund` 實際從 `market_cap_groups` 取值。這個 key 永不被讀取，可移除。

---

### 12. C14 proximity_max 5% 在 bull market 可能過嚴

5% 上限意味著股價必須在季線 ±5% 範圍內。在多頭市場中，強勢股可能已跑離季線 8-15%，全部被擋在 ENTER 門外。可考慮加入 `type_adjusted`（仿 ETF 做 type-aware）。

---

## 📊 總結

| # | 問題 | 優先級 | 類型 | 建議 |
|---|------|--------|------|------|
| 1 | C16 gain_max=30% vs README 80% | 🔴高 | 邏輯矛盾 | 確認意圖後統一 |
| 2 | C17 孤兒條件 (算但不計分) | 🔴高 | 設計缺失 | 加入 scoring/hard_reject |
| 3 | ETF manual 旗標偏誤 | 🔴高 | 計分錯誤 | manual→視為通過 |
| 4 | C10 回退 c10=c9 | 🟡中 | 邏輯過寬 | 預設 False |
| 5 | C8 填息未快取 | 🟡中 | 效能 | 加入 cache |
| 6 | E2 變數命名誤導 | 🟡中 | 維護性 | 重命名 |
| 7 | label_map 門檻不一致 | 🟡中 | UX | 動態取值 |
| 8 | ENTER 偏均值回歸 | 🟢低 | 設計選擇 | 確認意圖 |
| 9 | 殖利率 cutoff 太寬 | 🟢低 | 資料品質 | 降到 0.15 |
| 10 | Quick mode 不對稱 | 🟢低 | 設計觀察 | 注意即可 |
| 11 | C5 eps_min 死代碼 | 🟢低 | 清理 | 移除 |
| 12 | C14 5% 上限太嚴 | 🟢低 | 參數調校 | 考慮 type_adjusted |
