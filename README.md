# TW-Quant — 台股得分制篩選器 + ETF 雙軌篩選系統 + 找買點管線

本專案是一個基於 Python 的自動化台股篩選工具，結合了技術面、基本面、籌碼面與位階分析（**v5**：得分制、20 條件全景體檢、SQLite 快取）。

| 工具 | 進入點 | 說明 |
|------|--------|------|
| 個股得分制篩選 | `stock_screener.py` | 全市場 20 條件評分＋四層分類 |
| ETF 雙軌篩選 | `etf_screener.py` | 市值型／高股息／槓桿反向分類評分 |
| **找買點管線** | `pipeline_screener.py` | ETF 成分股股票池 → 100 分量化 → S/A/B 分級 → Top5 |

## 核心特性

- **20 條件評分系統**：將技術面(4)、基本面(5)、籌碼面(3)、位階(4)、買賣點(3) 加權計算（總分 100），不再是傳統的「全有全無」過濾。
- **四層輸出分類**：
  - 🟢 **ENTER (適合進場)**：得分 ≥ 75 且無 hard reject。
  - 🟡 **WATCH (追蹤價值)**：得分 40-74，趨勢偏多但尚欠攻擊訊號。
  - 🔴 **EXIT (出場訊號)**：從 K 線行為偵測轉弱訊號（E1~E5），不需持有紀錄。
  - ⚫ **OUT (不建議)**：得分 < 40 或觸發 hard reject 規則。
- **Hard Reject 機制**：C13 散戶接手、C16 翻倍、C17 高檔破月線、C20 空頭賣點任一觸發即直接 OUT，不讓它靠其他條件的分數混進 ENTER。
- **市值分群門檻**：自動根據公司市值（大型/中型/小型）套用不同的營收 YoY 與財務門檻。
- **優先級篩選**：自動按市值排序，優先處理權值股與高價值標的。
- **高效快取**：使用 SQLite 取代傳統 JSON，解決巨型資料載入效能問題。

## 資料源

| 資料類別 | 來源 |
|------|------|
| 日線 / 60 分線 | yfinance |
| 季報 (負債比、存貨) | yfinance Ticker |
| 季 EPS / 月營收 YoY | TWSE Open API (t187ap14_L / t187ap05_L) |
| 三大法人 / 融資券 | TWSE (fund/T86 / exchangeReport/TWT93U) |
| 千張大戶持股比率 | TDCC 集保 (自動網頁抓取 + 變更偵測) |

## 條件概覽 (個股)

| 類別 | 條件 | 權重 | 檢查內容 | 白話解讀 |
|------|------|------|----------|----------|
| **技術面** | C1 | 10 | 股價 > 60MA + 季線上升 | 股價已突破中期多空分水嶺（季線），且中期趨勢轉為翻揚向上，屬於典型的波段多頭格局初升段或續強訊號。 |
| | C2 | 5 | 股價 > 20MA | 股價站上短期多空分水嶺（月線），短期多頭格局確立，為最基本的多頭確認信號。 |
| | C3 | 5 | 近5日低價曾觸及月線（回測站回） | 短期拉回測試月線支撐，若能在此價位站回，代表回測成功，是技術分析中最經典的買進訊號。 |
| | C4 | 10 | 量能 > 5日均量 | 價漲有量，代表主力/法人願意追價，不是無量的空漲，價量配合才是健康的攻堅盤。 |
| **基本面** | C5 | 5 | 溢價 < 3%（收盤偏離合理價） | 現在股價跟公司內在價值（淨值/EPS推算）偏離不到3%，沒有明顯高估，進場安全邊際較高。 |
| | C6 | 5 | 市值分組後的 EPS/營收YoY/負債比達標 | 公司獲利能力（EPS）、成長動能（營收年增率）、財務穩健度（負債比）三合一檢核，避免踩到地雷股。 |
| | C7 | 10 | 存貨周轉天數未惡化 | 存貨沒有一堆在倉庫賣不掉（周轉天數不暴增=營運正常），會計做假帳的公司通常這裡會先爆炸。 |
| | C9 | 5 | EPS > 分組門檻 | 公司賺錢能力達到同市值級距的基本水準，確保不是「便宜但獲利衰退」的價值陷阱。 |
| | C10 | 5 | 營收 YoY > 分組門檻 | 公司營收年增率達到同市值級距的基本水準，確認成長動能沒有失速。 |
| **籌碼面** | C11 | 10 | 跌破均線時外資/投信連續買超 | 股價回檔時法人還在買，代表主力/法人認為這只是在洗盤，不是真的要出貨，籌碼安定。 |
| | C12 | 5 | 千張大戶比率不減反增 | 最有錢的那群股東（千張大戶）持股比例沒降反升，代表「聰明錢」看好後市，沒有在這裡倒貨。 |
| | C13 | 5 | 非散戶接手（法人大賣+融資增 → hard_reject） | 法人（外資+投信+自營商）在倒貨，但融資戶（散戶）在接刀，這是最危險的籌碼混亂訊號，直接淘汰不看。 |
| **位階** | C14 | 5 | 收盤距60MA 在 0~5%（中期位階） | 股價剛站上或微破季線（偏離不到5%），位階相對安全，不是已經漲到「乖離率過大」的危險區。 |
| | C15 | 5 | 連3日上漲（回測確認反轉） | K線連續3天收紅，確認前一波回測已結束、新一波攻堅開始，是回測後最明確的進場確認訊號。 |
| | C16 | 5 | 近40日未漲≥80%（未翻倍） | 近2個月漲幅還沒到翻倍（80%），排除已經狂漲一波、隨時會有獲利了結賣壓的過熱標的。 |
| | C17 | — | 高檔跌破月線（hard_reject） | 股價在近1個月高檔區（離高點≤15%），卻跌破月線，這是高檔反轉的危險訊號，直接淘汰。 |
| **買賣點** | C18 | 10 | 60分線黃金交叉 OR 日線帶量紅K | 60分K線的短均線向上穿越長均線（黃金交叉），或是日線出現「價漲+量增」的紅K棒，都是短線攻堅訊號。 |
| | C19_bonus | 5 | >60MA + KD黃金交叉（<20區）多頭買點加分 | KD指標在超賣區（<20）出現黃金交叉，且股價在季線之上，這是技術分析中最標準的「多頭初升段」買進訊號，通過才加分。 |
| | C20 | — | <60MA + KD死亡交叉（>80區）hard_reject | KD指標在超買區（>80）出現死亡交叉，且股價已在季線之下，這是「多頭困局、空頭初跌段」，直接淘汰。 |

**總分上限 100 分**，C19 是加分項（通過才加 5 分，不通過不扣）。

### C3 vs C14 差異

- **C3（回測20MA）**：低價碰月線 = 短期回測買點，lookback 5日
- **C14（距60MA proximity）**：收盤距季線百分比 = 中期位階判斷，MA60 長期
- 兩者互補：C3 抓短期支撐，C14 抓中期偏離度

### 基本面市值分群門檻

| 市值分組 | 營收 YoY | 負債比上限 | EPS 下限 |
|----------|----------|------------|----------|
| 大型股 (>5000億) | > 5% | < 60% | > 1.0 |
| 中型股 (>500億) | > 10% | < 50% | > 0.5 |
| 小型股 (<500億) | > 15% | < 45% | > 0.0 |

### C13 散戶接手判斷（hard_reject）

1. **主邏輯**：近3日三大法人（外資+投信+自營商）合計賣超 + 融資餘額增加 → 散戶接刀
2. **替代信號**：自營商大買 + 融資大增（自營商買超 > 融資增額 × 50%）→ 疑似散戶透過自營商避險帳戶進場

### 出場信號（E1~E5）

| 信號 | 觸發條件 |
|------|----------|
| E1 | 前30日曾站上20MA → 現跌破月線 |
| E2 | 近高檔反轉 + KD死叉 |
| E3 | 跌破60MA + 60MA走平/向下 |
| E4 | 連跌4日 + 量縮<5MA 70% |
| E5 | 連3日量縮<5MA 60% + 價穩在月線上（主力退場前兆） |

### Hard Reject 規則（觸發任一即直接 OUT）

| 規則 | 條件 | 意義 |
|------|------|------|
| C16 翻倍 | 近40日漲幅 ≥ 80% | 已過熱 |
| C17 高檔破月線 | 價位近年高 + 跌破20MA | 高檔反轉 |
| C20 空頭賣點 | 收 <60MA + KD死叉>80區 | 趨勢轉空 |
| C13 散戶接手 | 近3日三大法人賣超 + 融資增 | 散戶接刀 |

## 實際測試結果（2026-06-12）

| 股票 | 分數 | 分類 | Hard Reject | 出場信號 |
|------|------|------|-------------|----------|
| 2330 台積電 | 50 | 🔴 EXIT | ✅ C13散戶接 | E1多頭破月線 |
| 2317 鴻海 | 45 | 🔴 EXIT | ✅ C13散戶接 | E1多頭破月線 |
| 2454 聯發科 | 55 | ⚫ OUT | ✅ C13散戶接 | — |
| 2881 富邦金 | 35 | ⚫ OUT | ✅ C13散戶接 | — |

## 安裝與執行

### 環境要求
- Python 3.9+（開發環境為 3.14；找買點管線建議 3.13+）
- 依賴安裝：
```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### 自動啟用venv腳本
複製此腳本貼入~/.bash_profile
```
# Python3.14
export PATH="/opt/homebrew/opt/python3/bin:$PATH"
# 自動啟用 Python 虛擬環境 (venv)
function cd() {
    builtin cd "$@"
    # 檢查當前目錄下是否存在 .venv 或 venv 資料夾
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    elif [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    else
        # 如果離開了專案目錄且目前已啟用虛擬環境，則自動停用 (deactivate)
        # 注意：排除包含 VIRTUAL_ENV 的父目錄路徑，避免在子目錄間移動時一直重啟
        if [ -n "$VIRTUAL_ENV" ]; then
            if [[ "$PWD" != "${VIRTUAL_ENV%/*}"* ]]; then
                deactivate
            fi
        fi
    fi
}
```
重開終端或source ~/.bash_profile

### 執行篩選
```bash
# 執行台股篩選
python3 stock_screener.py

# 執行 ETF 篩選
python3 etf_screener.py
```

### 檢視結果
- **終端機**：顯示即時篩選明細與分級摘要。
- **CSV 報告**：自動儲存於 `screening_results/screening_YYYYMM.csv`。
- **日誌**：詳盡除錯日誌儲存於 `logs/`。

---

## 維護工具

### `dump_candidates.py` — 候選清單自動產生器

從 TWSE 公開 API 自動產出四份候選清單（含名稱），並自動排除已下市或已轉型的標的。

```bash
# 完整執行（直接寫入）
python3 dump_candidates.py

# 僅比對新舊差異，不寫入
python3 dump_candidates.py --check
```

**輸出檔案**：

| 檔案 | 來源 | 代碼數 |
|------|------|--------|
| `data/candidates.csv` | TWSE t187ap14_L（去除 ETF/ETN/REAT）| ~1,079 |
| `candidates_ETF.csv` | TWSE `/rwd/zh/ETF/list`（含 L/R/多幣別）| ~229 |
| `candidates_ETN.csv` | TWSE `/rwd/zh/ETN/list` | ~15 |
| `candidates_REAT.csv` | twstock 不動產證券化分類 | ~6 |

**設計特點**：
- ETF 代號自動處理多幣別報價（如 `006205(新臺幣)<br>00625K(人民幣)` → 兩個代號）
- 股票池動態排除 ETF + ETN + REAT，不需手動維護名單
- `--check` 模式可檢視新增/移除代號，確認新掛牌或下市

---

## 檔案結構

```
tw-quant/
├── stock_screener.py        # 個股篩選主程式
├── etf_screener.py          # ETF 雙軌篩選器
├── dump_candidates.py       # 候選清單自動產生器（TWSE API）
├── common/
│   ├── __init__.py
│   ├── etf_yahoo.py         # ETF 持股動態抓取（Yahoo Finance TW）
│   ├── cache.py             # SQLite 磁碟快取
│   ├── kd.py                # KD 計算（含 warmup + 平滑參數）
│   ├── rate_limit.py        # 執行緒安全 RateLimiter
│   ├── scoring.py           # 得分制、硬淘汰、四層分類、出場信號(E1~E5)
│   ├── tdcc.py              # TDCC 集保（執行緒安全）
│   └── yf_utils.py          # yfinance 批次下載、get_stock_info(4-tier)
├── config.json              # 個股篩選參數
├── config_etf.json          # ETF 篩選參數
├── data/                    # 候選清單（動態產出，dump_candidates.py 維護）
│   ├── candidates.csv       # 股票候選（~1079 檔）
│   ├── candidates_ETF.csv   # ETF 候選（~229 檔）
│   ├── candidates_ETN.csv   # ETN 候選（~15 檔）
│   └── candidates_REAT.csv  # REAT 候選（~6 檔）
├── etf_top10_holdings.py    # 獨立腳本：快速查詢單檔 ETF 前十持股
├── tests/                    # pytest 單元測試（43 tests）
└── screening_results/        # 篩選結果 CSV（每月一個）
```

**附：快速查詢 ETF 持股**

```bash
python3 etf_top10_holdings.py
# 輸入：0050
# 輸出：0050 前十持股 ticker + 名稱 + 權重
```

`etf_top10_holdings.py` 為獨立互動腳本（無需候選清單），直接爬 Yahoo Finance TW 頁面，適用快速查詢特定 ETF 的成分股。

---

## 找買點管線（pipeline_screener.py）

三段式漏斗：**ETF 成分股股票池 → 100 分量化評分 → 硬淘汰＋S/A/B 分級 → Top5 買點清單**。

### 流程

```
Stage 0  股票池建構：ETF 候選清單近三年「純價格報酬」排名取前五
         → 前十大持股合併去重（≥50 檔）→ MoneyDJ 產業別標記 → data/universe.csv
Stage 1  100 分量化評分：
         因子① 基本面成長 25｜② EPS 預估上修 30（真實券商共識）｜
         ③ 法人/主力籌碼 20｜④ 波段動能 15｜⑤ 股價低位階 10
         → 表一（全量量化表，含進場區/停損/目標價/R/R）
Stage 2  硬淘汰（H1–H5）→ S/A/B 訊號分級 → 表二（Top10）與 Top5 買點清單
```

### 訊號標籤

| 訊號 | 條件 |
|------|------|
| 🟢 研究進場 | R/R≥2 且 f2≥24 且 f3≥14 且 2027 成長>0 |
| 🟡 等待買點 | f2≥18 且 2027 成長>0 且 R/R≥1.5 且 法人尚未轉買 |
| 🟠 股價過高 | total≥60 但條件未達 S/A |
| ⚪ 資料缺失 | total<60（含回補前資料不全者） |
| 🔴 淘汰 | 觸發硬淘汰規則 H1–H4 |

### 執行

```bash
python3 pipeline_screener.py                    # 完整流程（首次約 10~20 分鐘）
python3 pipeline_screener.py --rebuild-universe # 強制重建股票池
python3 pipeline_screener.py --top 10           # 量化表保留前 N 名
python3 pipeline_screener.py --dry-run          # 驗證設定，不發網路請求
```

### 輸出（screening_results/pipeline_YYYYMMDD.md ＋ CSV）

一、Top5 買點清單｜二、表二 Top10 量化表｜三、表一全量量化表（附錄）｜
四、淘汰名單（含規則編號與說明）｜五、資料源統計｜訊號分級定義｜七、欄位計算說明稽核附錄（公式＋每檔子項數值）

另有 `pipeline_*_full.csv / _top10.csv / _detail.csv` 三份機器可讀檔。

### 資料源與備援

| 數據 | 主路徑 | 備援 |
|------|--------|------|
| EPS 預估／上修／產業對比／目標價 | yfinance | —（唯一免費源） |
| 三大法人買賣超 | TWSE fund/T86 | FinMind InstitutionalInvestorsBuySell |
| 月營收／財報／現金流 | TWSE OpenAPI | FinMind |
| 日線 K 檔 | yfinance | FinMind TaiwanStockPrice |
| 族群分類 | MoneyDJ（Big5 解析） | FinMind TaiwanStockInfo |
| 大戶持股 | TDCC 集保 | — |

### 快取與回補

- 成功取得的資料快取 12~24 小時（SQLite），重跑秒級完成、不消耗額度
- 抓取失敗或資料缺失者**不入快取**——下次執行自動重試回補

### AI 質性覆核（選配）

Top5 清單最後一欄「AI評估」可由 LLM 做風控覆核（不得更改分級，僅 ≤80 字觀察）。設定 `config_pipeline.json` 的 `ai_review` 區塊：

**自動啟用**：環境變數 `OPENAI_API_KEY`／`OPENAI_BASE_URL`／`OPENAI_MODEL` 三個齊備即生效（本地端點可不設 API key），無需改設定檔。選配項：

```jsonc
// config_pipeline.json 的 ai_review 區塊（全部可省略）
"ai_review": {
  // "enabled": false,          // 明確設 false 可強制關閉
  // "base_url": "...",         // 環境變數未設時的備用
  // "model": "...",
  "temperature": 0.2,
  "system_prompt": "你現在是一名資深台股量化投資分析師……"
}
```

### 診斷

```bash
python3 scripts/check_data_sources.py   # 一鍵檢查全部 6 個資料源連線
```

### 規格與演算法

`~/tasks/tw-quant/spec.md` 與 `algs/*.md`（stage0-universe／factor-scoring／entry-stop-target／signal-grading）

---
## License

本專案採用 **Apache License 2.0** 授權。

- 完整授權條款見 [`LICENSE`](LICENSE)（專案根目錄）
- Apache-2.0 官方條款：<https://www.apache.org/licenses/LICENSE-2.0>
- 版權與貢獻者資訊以 LICENSE 檔案為準

> 本專案為研究/模擬用途，授權條款不構成任何投資建議或保證；
> 使用/修改/再散佈前請詳閱 LICENSE 全文。

本專案僅供個人量化研究與教育用途。資料來源（FinMind、TWSE、TPEX）之使用請遵守各平台之服務條款。

Proprietary - All rights reserved.
