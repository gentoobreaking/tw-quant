# TW-Quant 台股/ETF 篩選系統 — 改善計劃

> 合併來源：`improvement.md`（Phase 1，P0–P4）+ `improvement-2.md`（Phase 2–3，已合併至本檔）
> 合併日期：2026-06-11
> 合併原則：照時間先後順序，H/M/L 稽核項目統一指向 `data-correctness-audit.md`

---

## 狀態總覽

| Phase | 內容 | 狀態 |
|-------|------|------|
| **Phase 1** | P0–P4 改善藍圖（`improvement.md`） | ✅ 全部完成 |
| **Phase 2** | 效能、邏輯強健化、工程品質 | ✅ 全部完成 |
| **Phase 3** | Phase 2 之後發現的實作缺口（S1–S3、ET-1） | ✅ 全部完成 |
| **附錄** | H/M/L 資料正確性稽核 | 見 `data-correctness-audit.md` |

---

## Part I — Phase 1：改善藍圖

> 來源：`improvement.md`（2026-06-10）| 全項已完成

### 執行順序總表

| 優先 | 項目 | 時程 | 理由 |
|------|------|------|------|
| **P0** | C11 籌碼對齊 bug | 1h | **邏輯 bug**，結果根本不可靠 |
| **P0** | 位階重新設計：C14/C15 對齊「60MA 回測買點」 | 半天 | 核心策略不符合實際需求 |
| **P1** | C8 冗余移除 | 15m | 零篩選力 |
| **P1** | C3 lookback_days 2→5 | 5m | 太短形同虛設 |
| **P1** | ETF C5 溢價門檻放寬 + 幣別處理 + 折價加分 | 1h | 現行太嚴且有失真 |
| **P1** | C15 分母改用 ATR（已被 P0-2 取代）| — | P0-2 重新設計後已不存在該問題 |
| **P1** | ETF C7/C8 誠實標示 | 15m | 消除誤導 |
| **P1** | KD warmup + C18 抽出 | 2h | 避免短資料失真 |
| **P2** | 共用模組抽取 + 快取改 SQLite | 1d | 後續所有改動的基礎 |
| **P2** | 得分制 + 三層輸出（ENTER/WATCH/EXIT）| 1d | 與位階重新設計綁定 |
| **P2** | 基本面按市值分群門檻 | 半天 | 提升基本面條件區分力 |
| **P3** | C16 門檻合理化 | 30m | 配合位階重設 |
| **P3** | ETF C6 改為動態持股抓取 | 2h | 提升區分力 |
| **P3** | Logging + 結果持久化 | 1d | 提升除錯效率 |
| **P3** | TDCC 頁面變更偵測 | 30m | 降低靜默失敗風險 |
| **P4** | 型別提示 | 1d | 提升程式碼維護性 |
| **P4** | 單元測試 | 2–3d | 建立基礎測試框架（pytest）|
| **P4** | 批次優先級排序 | 半天 | 效能優化 |

---

### P0：必須立即修復

#### P0-1. C11 籌碼對齊 bug ✅ 已修復

**位置**：兩 screener 的 `get_chip()` + `check_chip()`

**問題**：`get_chip` 只取近 6 個交易日的法人買賣超，但 `check_chip` 用 `below_ma` 的 list 長度推算 `fn` 的起始位置，日期完全沒有對齊。

**修法**：在 `get_chip` 中保留日期資訊，在 `check_chip` 中按日期 join。

---

#### P0-2. 位階重新設計：C14/C15 對齊「60MA 回測買點」✅ 已修復

**問題**：原 C14（跌幅≥30%）和 C15（橫盤≥90日）是股災抄底邏輯，與 C1–C4（上升趨勢）AND 連接，幾乎不可能同時通過。

**新設計**：
- **C14**：收盤價在 60MA 上方 0–5% 範圍內
- **C15**：近 3 日連續上漲（或近 5 日至少 3 漲 + 今日上涨）
- **C16**：門檻下修（個股 80%→30%、ETF 配合調整）
- C18/C19/C20 改為加分項，不影響通過與否

---

### P1：應盡快修復

| 項次 | 名稱 | 狀態 |
|------|------|------|
| P1-1 | C8 冗余移除 | ✅ 已修復 |
| P1-2 | C3 lookback_days 2→5 | ✅ 已修復 |
| P1-3 | ETF C5 溢價門檻放寬 + 幣別處理 + 折價加分 | ✅ 已修復 |
| P1-4 | C15 分母改用 ATR | ✅ 已廢止（P0-2 取代）|
| P1-5 | ETF C7/C8 誠實標示 | ✅ 已修復 |
| P1-6 | KD warmup + C18 抽出為獨立函式 | ✅ 已完成 |

---

### P2：重要改善

#### P2-1. 共用模組抽取 ✅ 已完成

目錄結構：
```
common/
├── __init__.py
├── config.py       # 設定檔載入、deep merge、CONFIG 全域
├── cache.py        # 磁碟快取（SQLite + 記憶體 mirror）
├── rate_limit.py   # 各資料源 rate limiter
├── tdcc.py         # TDCC 集保查詢（執行緒安全）
├── yf_utils.py     # yfinance 批次下載、get_stock_info（4-tier）
├── kd.py           # KD 計算（可設定 k_smooth/d_smooth + warmup）
└── scoring.py      # calc_score、check_hard_reject、classify_tier、check_exit
stock_screener.py   # 股票專屬條件 + 主流程
etf_screener.py     # ETF 專屬條件 + 主流程（含 dual-track）
config.json         # 股票篩選參數
config_etf.json     # ETF 篩選參數
```

#### P2-2. 得分制 + 三層輸出 ✅ 已完成

- 權重設計（總分 100）
- 硬淘汰（HARD_REJECT：一票否決）
- 三層分類：ENTER（≥75）、WATCH（40–74）、EXIT/OUT

#### P2-3. 基本面按市值分群門檻 ✅ 已完成

| 市值等級 | rev_yoy_min | debt_ratio_max | eps_min |
|---------|-------------|----------------|--------|
| 大型股（>5000億）| 5.0 | 60.0 | 1.0 |
| 中型股（>500億）| 10.0 | 50.0 | 0.5 |
| 小型股（<500億）| 15.0 | 45.0 | 0.0 |

---

### P3：改善項目

| 項次 | 名稱 | 狀態 |
|------|------|------|
| P3-1 | C16 門檻合理化（個股 80%→30%）| ✅ 已修復 |
| P3-2 | ETF C6 改為動態持股抓取（`fetch_top10_holdings()`）| ✅ 已修復 |
| P3-3 | Logging + 結果持久化 | ✅ 已完成 |
| P3-4 | TDCC 頁面變更偵測（6 特徵點結構檢查）| ✅ 已完成 |
| P3-5 | ETF `classify_etf` 殖利率格式判斷不穩健 | ✅ 已修復（M2）|
| P3-6 | ETF `quick_mode` 不應跳過基本面 | ✅ 已完成 |

---

### P4：長期優化

| 項次 | 名稱 | 狀態 |
|------|------|------|
| P4-1 | 型別提示（TypedDict / dataclass）| ✅ 已完成 |
| P4-2 | 單元測試（pytest，43 tests）| ✅ 已完成 |
| P4-3 | 批次優先級排序（`screen_batch_prioritized`）| ✅ 已完成 |

---

## Part II — Phase 2 + 3：進階優化

> 來源：`improvement.md` Phase 2 內容（2026-06-10 建立，2026-06-11 更新）| 全部完成

### Phase 2 狀態總表

| 項次 | 名稱 | 狀態 |
|------|------|------|
| 1-1 | 財務報表快取（SQLite + 7 天 TTL）| ✅ |
| 1-2 | ETF 持股動態抓取（`fetch_top10_holdings()`）| ✅ |
| 1-3 | 並行處理（ThreadPoolExecutor + RateLimiter + TDCC Lock）| ✅ |
| 2-1 | 市值備援（`get_stock_info()` 4-tier）| ✅ |
| 2-2 | 設定檔集中化（`config.json`/`config_etf.json`）| ✅ |
| 2-3 | TDCC 解析強健化（6 特徵點結構檢查）| ✅ |
| 3-1 | 視覺化進度條（tqdm）| ✅ |
| 3-2 | 終端機顏色標示（colorama）| ✅ |
| 4-1 | 擴充單元測試（43 tests）| ✅ |
| 4-2 | CI/CD 整合（requirements.txt + GitHub Actions）| ✅ |
| 4-3 | pyproject.toml 專案中繼資料 | ✅ |
| 4-4 | GitHub Issue / PR 模板 | ✅ |

### 資料正確性稽核（H 級修復）✅

> 詳細內容見 [`data-correctness-audit.md`](data-correctness-audit.md)

| 項次 | 問題 | 狀態 |
|------|------|------|
| H1 | `config.json` mid cap 閾值 500B = large | ✅ 修復為 50B |
| H2 | `_T86_CACHE`/`_T93_CACHE` 無 Lock | ✅ 加入 `_TWSE_LOCK` |
| H3 | `_ETF_TOP_HOLDINGS` 硬編碼佔位符 | ✅ 改用 `fetch_top10_holdings()` |
| H4 | `get_fund` bare `except: pass` | ✅ 改為分層 logger |
| H5 | C6/C7/C8 `manual` 旗標未呈現 | ✅ `screen_one` + `print_summary` 顯示 |
| H6 | `kd_check_offset: 2` → 金叉用 -3/-2 | ✅ 改為 `1`（使用最新 bar）|
| H7 | `_track_totalassets` 無法更新舊值 | ✅ 改用 `save_disk_cache()` 強制寫入 |

### 資料正確性稽核（M 級修復）✅

> 詳細內容見 [`data-correctness-audit.md`](data-correctness-audit.md)

| 項次 | 問題 | 狀態 |
|------|------|------|
| M1 | `deep_merge` 原地修改 `base` | ✅ `load_config` 先 deepcopy |
| M2 | 殖利率啟發式（`val > 0.5`）| ✅ 歸零重置 |
| M3 | E1 排除最近 10 天 | ✅ 含最新 bar |
| M4 | 存貨天數除數固定 91.25 | ✅ 依 COGS 平均間距動態判斷 |
| M5 | C13 `tn` 長度未檢查 | ✅ 加入 `len(tn) >= ld` |
| M6 | 60-min cache TTL 7200s 收盤前 stale | ✅ 已知，低影響 |
| M7 | `tier_thresholds`/`exit_params` 未進 `_DEFAULTS` | ✅ 兩 screener 皆已補進 |

### Phase 3 實作項目（已全部完成）

| 項次 | 名稱 | 修改位置 |
|------|------|----------|
| **S1** | `get_fund()` 改呼叫 `get_stock_info()`（4-tier，含 `.TWO` + 資產負債表）| `stock_screener.py:267` |
| **S2** | `screen_batch_prioritized()` 並行 `fetch_top10_holdings()`（非 lazy）| `etf_screener.py:1001-1019` |
| **S3** | `_track_totalassets()` 加入 `_ASSET_LOCK` 防 race condition | `etf_screener.py:176, 488-494` |
| **ET-1** | `totalAssets=None` 導致 REAT/ETN 誤判跳過 → 改為 `info is None` 才跳 | `etf_screener.py:979` |

---

## Part III — 維護工具

### `dump_candidates.py` — 候選清單自動產生器

**用途**：從 TWSE 公開 API 自動產出四份候選清單並含名稱欄。

**使用方式**：
```bash
python3 dump_candidates.py        # 直接寫入（覆寫）
python3 dump_candidates.py --check  # 只比對差異，不寫入
```

**資料來源**：

| 檔案 | 來源 API | 代碼數 |
|------|----------|--------|
| `candidates.csv` | TWSE t187ap14_L（公司代號+名稱）| ~1,079 |
| `candidates_ETF.csv` | TWSE `/rwd/zh/ETF/list`（含 L/R/多幣別解析）| ~229 |
| `candidates_ETN.csv` | TWSE `/rwd/zh/ETN/list` | ~15 |
| `candidates_REAT.csv` | twstock `受益證券-不動產投資` 分類 | ~6 |

**輸出格式**（四檔統一）：
```
ticker,name
1101,臺灣水泥股份有限公司
0050,元大台灣50
020000,富邦特選蘋果N
01001T,土銀富邦R1
```

**設計說明**：
- ETF 代號處理：解析 `<br>` 分隔的多幣別報價（如 `006205(新臺幣)<br>00625K(人民幣)`），並擷取 `(幣別)` 後綴
- REAT 偵測：從 twstock `受益證券` type 中過濾含「不動產」的項目（排除 ABS/資產基礎證券）
- 股票池 = t187ap14_L 全部公司代號 − ETF − ETN − REAT
- `--check` 模式可檢視新舊差異（新增/移除代號），不影響現有檔案

---

## 附錄：H/M/L 詳細稽核項目

> 完整內容見 [`data-correctness-audit.md`](data-correctness-audit.md)

| 等級 | 數量 | 內容 |
|------|------|------|
| H（高）| 7 | config 閾值錯誤、執行緒安全、硬編碼、過度廣域 exception、manual 未呈現、KD offset、追蹤寫入 |
| M（中）| 7 | deep_copy、殖利率判斷、E1 區間、存貨除數、C13 邊界、60-min stale、DEFAULTS 缺少 |
| L（低）| 11 | 見 `data-correctness-audit.md` |

---

## 總結

- **Phase 1**（P0–P4，藍圖階段）：全部完成
- **Phase 2**（效能/邏輯/工程品質）：全部完成
- **Phase 3**（實作缺口修補）：全部完成
- **新工具**：`dump_candidates.py` 已建立
- **待關注**：L1–L11（低優先級，見 `data-correctness-audit.md`）