# 資料正確性稽核報告

> 產生日期: 2026-06-10
> 範圍: stock_screener.py、etf_screener.py、common/\*、config.json、config_etf.json

---

## 🔴 高嚴重度

### H1. `config.json` mid cap 閾值寫錯

| 項目 | 內容 |
|------|------|
| **檔案** | `config.json:20` |
| **問題** | `mid: 500_000_000_000` 和 `large`（line 13）完全相同 — 都是 5000 億，`mid` 分組永遠不可抵達。 |
| **影響** | 市值 500 億～5000 億的中型股拿到錯誤的基本面門檻（被歸入 `small` 規則）。正確值應為 `50_000_000_000`（500 億）。 |
| **預設值比對** | `stock_screener.py:79` 中的預設 `mid: 50_000_000_000` 是正確的，但被 `config.json` 的 `deep_merge` 覆蓋。 |

---

### H2. TWSE API module-level cache 無執行緒保護

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:158-179`、`etf_screener.py:184-205` |
| **問題** | `_T86_CACHE` 和 `_T93_CACHE` 是模組層級 plain `dict`，被 `ThreadPoolExecutor` 多執行緒同時讀寫，完全沒有 `threading.Lock()` 保護。 |
| **影響** | 並發篩選時可能發生：資料丟失、`KeyError`、`RuntimeError: dictionary mutated during iteration`。 |

---

### H3. ETF 成分股硬編碼嚴重過期／佔位符

| 項目 | 內容 |
|------|------|
| **檔案** | `etf_screener.py:63-82` |
| **問題** | 多檔 ETF 共用同一組佔位符（0051/0052/0053/0055 全部用 `["2330", "2454", "2308"]`），明顯複製貼上。部分 ETF（006205、006206、006207）使用 `[""]` 空字串。 |
| **影響** | C6（成分股 ≥2/3 在季線上）對多數 ETF 不可靠。0056、00878 等每年換股，硬編碼完全跟不上。 |

---

### H4. `get_fund` 沉默吞掉所有例外

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:288` |
| **問題** | 整個 yfinance 財報區塊（lines 248-289）包在 `except: pass` 中。網路錯誤、API 格式變更、KeyError、除零錯誤全部沉默。 |
| **影響** | 財報缺失的股票拿到全 `None` 的 info，`check_fund` 用 `None` 做預設判斷，可能在無警報的情況下錯誤地通過或淘汰。 |

---

### H5. 高股息 ETF 的 C7/C8 `manual: True` 從未被呈現

| 項目 | 內容 |
|------|------|
| **檔案** | `etf_screener.py:326, 333` |
| **問題** | `check_c7()` 和 `check_c8()` 對高股息 ETF 回傳 `{"ok": True, "manual": True}`，但 `manual` 旗標只存在 dict 裡，整條 pipeline 無人讀取。使用者看到 `C7=Y` / `C8=Y` 但不知道需要手動查驗。 |
| **影響** | 高股息 ETF 可能因 C7/C8 無條件通過而進入 WATCH/ENTER 分級。 |

---

### H6. KD 金叉／死叉使用 -3/-2 位置而非最新一根 bar

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:616-628`、`etf_screener.py:636-646` |
| **問題** | `kd_off = 2` 導致金叉判斷在倒數第 3 根和倒數第 2 根 bar 之間進行，最新的 bar（position -1）完全被忽略。 |
| **影響** | 今天剛發生的金叉／死叉要等到下一根 bar 才被偵測到，C19/C20 存在 off-by-one 時序誤差。 |

---

### H7. ETF `_track_totalassets` 無法更新前一筆值

| 項目 | 內容 |
|------|------|
| **檔案** | `etf_screener.py:498` |
| **問題** | `cache.get(prev_ck, lambda: current_ta)` — 若 `prev_ck` 已在 cache 中且未過期，`fetch_fn` **不會被呼叫**，新的 `current_ta` 無法寫入。資產增減趨勢比較永遠停留在第一次寫入的值。 |
| **影響** | C12 的資產規模趨勢判斷可能基於過時資料。 |

---

## 🟡 中嚴重度

### M1. `deep_merge` 原地修改 defaults dict

| 項目 | 內容 |
|------|------|
| **檔案** | `common/config.py:6-13` |
| **問題** | `deep_merge(base, override)` 直接修改 `base` 物件。`load_config()` 傳入模組層級的 `_DEFAULTS`，若被多次呼叫，預設值會被已合併的資料汙染。 |
| **建議** | 進入 `deep_merge` 前先 `copy.deepcopy(base)`。 |

---

### M2. `classify_etf` 殖利率啟發式脆弱

| 項目 | 內容 |
|------|------|
| **檔案** | `etf_screener.py:160-165` |
| **問題** | yfinance 的 dividendYield 有時回傳比率（0.05 = 5%）、有時回傳百分比（5.0）。程式碼用 `dy < 1.0` 猜測格式，但若 yfinance 回傳不明中間值（如 0.5），會誤分類為高股息（實際可能是 0.5% 或 50%）。 |
| **影響** | 少數 ETF 可能被錯誤分類。 |

---

### M3. E1 出場訊號排除最後 10 天

| 項目 | 內容 |
|------|------|
| **檔案** | `common/scoring.py:160-166` |
| **問題** | E1 檢查區間為 `[max(0, len-30), len-10)`，排除最近 10 天的資料。若股價在近 10 日內才剛跌破月線，E1 不會觸發。 |
| **影響** | 出場訊號可能延遲。 |

---

### M4. 存貨天數假設季度資料但除數固定 91.25

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:283` |
| **問題** | `inv.loc[d] / (cogs.loc[d] / 91.25)` — 91.25 = 365/4，假設季度 COGS。若 yfinance 回傳年報（如已下市股票），除數差約 4 倍。 |
| **影響** | 存貨天數可能被低估或高估 4 倍。 |

---

### M5. C13 未檢查 `tn`（信託淨買超）長度

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:431`、`etf_screener.py:427` |
| **問題** | `recent_fnet2` 計算用 `tn[-ld:]`，但長度檢查只涵蓋 `fn` 和 `mb`。若 `tn` 比預期短（部分日期無信託資料），`tn[-ld:]` 回傳較短的 slice，加總值偏小。 |
| **影響** | C13 判斷可能因此誤判為散戶接手（不安全）。 |

---

### M6. 60-min KD 交叉 stale index

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:501-557`、`etf_screener.py:516-564` |
| **問題** | 60 分鐘資料 cache TTL 預設 7200 秒。若上午 10:00 執行篩選、下午 2:00 再次執行，盤中已變化的 KD 交叉狀態不會被更新。 |
| **影響** | 盤中第二次篩選使用過時的技術面狀態。 |

---

### M7. 部分 config key 不在 `_DEFAULTS` 中

| 項目 | 內容 |
|------|------|
| **檔案** | `stock_screener.py:722-727`、`etf_screener.py:723-728` |
| **問題** | `tier_thresholds` 和 `exit_params` 透過 `CONFIG.get("key", {...})` 存取，但這些 key 不在 `_DEFAULTS` dict 中。若設定檔遺失該區段，inline 預設值會正確生效，但與整體設定檔策略不一致。 |

---

## 🟢 低嚴重度

| 項目 | 說明 | 位置 |
|------|------|------|
| L1 | `get_exchange` 若 twstock 無法導入（未安裝或例外），全部股票預設為 `.TW`（上市），OTC 股會拿到錯誤後綴而失敗 | `common/yf_utils.py:241-250` |
| L2 | 資產負債表 marketCap 備援硬編 TWD 10 面額 — 部分股票面額為 1/5/0.1 元，股數和市值估算會錯 | `common/yf_utils.py:173-174` |
| L3 | `candidates.csv` 中的 `7788` 非已知台股代號（靜默跳過） | `candidates.csv:8` |
| L4 | `save_results` CSV schema 直接取 `ScreeningResult.__dataclass_fields__`，增減欄位時 schema 會變，跨月 CSV 不相容 | `common/scoring.py:258` |
| L5 | `dict_to_df` 索引還原啟發式 — 若 DataFrame 原為整數索引，但字串恰好半數以上可 parse 為 datetime，會誤轉為 datetime 索引 | `common/serialization.py:34-53` |
| L6 | TDCC 版位檢查只 warn 不中斷 — 若格式大幅變動，parser 靜默回傳 `None` | `common/tdcc.py:81-87, 124-126` |
| L7 | 60-min 資料預設 cache TTL 7200s — 若初次 fetch 失敗，`skip_none=True` 不 cache，每次都會重試 | `common/cache.py` 搭配 `yf_utils.py` |
| L8 | rate limiter jitter 可能產生負延遲 — `cfg["delay"] < cfg["jitter"]` 時發生（目前 config 正常） | `common/rate_limit.py:24` |
| L9 | `fetch_financials` TTL 硬編碼 604800s（7 天），不從 config 讀取 | `common/yf_utils.py:193` |
| L10 | cache cleanup 閾值為 `2 * self.ttl` — 過期資料在資料庫中多存 1 個 TTL 週期 | `common/cache.py:89-93` |
| L11 | `to_json_val` 將未處理型別透過 `str()` 轉換 — datetime、tuple 等型別資訊永久遺失 | `common/serialization.py:21` |

---

## 優先修復建議

| 順序 | 項目 | 原因 |
|------|------|------|
| 1 | **H1** — 修正 `config.json` mid cap 閾值 | 一行設定錯誤導致中型股永遠用錯規則 |
| 2 | **H2** — 為 TWSE cache 加上 Lock | 多執行緒下資料可能隨時損毀 |
| 3 | **H4** — `get_fund` 取代 `except: pass` 為明確處理 | 財務資料錯誤完全沈默 |
| 4 | **H5** — 在介面呈現 `manual` 旗標 | 高股息 ETF 使用者被誤導 |
| 5 | **H6** — 修正 KD 位置從 -3/-2 改為 -2/-1 | 金叉訊號延遲一筆 |
| 6 | **H7** — `_track_totalassets` 改為強制寫入 | 資產趨勢永遠 stale |
| 7 | **M1** — `deep_merge` 加入 `copy.deepcopy` | 避免副作用汙染預設值 |
