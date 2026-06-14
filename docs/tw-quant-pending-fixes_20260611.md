# TW-Quant 待處理項目 — 具體修改方案

**日期**: 2026-06-11
**基於**: stock_screener.py / etf_screener.py / config.json / config_etf.json / common/scoring.py 最新版

---

## 🔴 修改 1：C13 散戶接加入硬淘汰

### 問題
C13（散戶接手：法人大賣+融資增）目前只是扣分項（5分），不是硬淘汰。散戶接手是明確的風險信號，與 C20（空頭賣點）嚴重程度相當，但 C20 是 `hard_reject: true` 而 C13 不是，邏輯不對稱。

### 修改檔案

#### 1.1 `config.json` — hard_reject_rules 加入 c13
```json
"hard_reject_rules": {
    "c16": false,
    "c20": true,
    "c13": true
}
```

#### 1.2 `config_etf.json` — 同步修改
```json
"hard_reject_rules": {
    "c16": false,
    "c20": true,
    "c13": true
}
```

#### 1.3 `common/scoring.py` — 更新預設值
```python
DEFAULT_HARD_REJECT_RULES = {"c16": False, "c20": True, "c13": True}
```

#### 1.4 兩 screener 的 `_DEFAULTS` — 同步更新
```python
# stock_screener.py
"hard_reject_rules": {
    "c16": False,
    "c20": True,
    "c13": True    # 新增
},

# etf_screener.py
"hard_reject_rules": {
    "c16": False,
    "c20": True,
    "c13": True    # 新增
},
```

#### 1.5 `check_hard_reject` 邏輯確認
現有 `check_hard_reject` 的邏輯是：
```python
def check_hard_reject(conditions: dict, rules: dict = None) -> bool:
    for key, expected in rules.items():
        if conditions.get(key) == expected:
            return True
    return False
```
C13 的 `conditions["c13"]` 在 `_build_result` 中設定為 `chip.get("c13", True)`，其中 `True` 代表「安全（非散戶接）」。

所以 `c13: True` 的 `hard_reject` 規則需要設定為 `"c13": False`，意即「當 c13=False（觸發散戶接）時硬淘汰」。

**修正**：config 中應寫 `"c13": false`（不是 true），因為：
- `conditions["c13"] = True` → 安全，不淘汰
- `conditions["c13"] = False` → 散戶接手，硬淘汰
- `rules["c13"] = False` → 當 c13==False 時觸發淘汰 ✅

```json
"hard_reject_rules": {
    "c16": false,
    "c20": true,
    "c13": false
}
```

同步修改 `DEFAULT_HARD_REJECT_RULES` 和兩個 `_DEFAULTS`。

#### 1.6 README 更新
在條件總表中 C13 描述更新：
```
C13 散戶接排除：法人大賣但融資大增 → 硬淘汰
```

### 影響評估
- 散戶接手的股票將直接歸入 OUT，不再只是扣 5 分
- 預期 ENTER 數量略微減少（淘汰了籌碼面有風險的標的）
- 與 C20 空頭賣點的嚴重度對齊，邏輯一致

---

## 🔴 修改 2：C19 多頭買點計入個股版評分

### 問題
ETF 版有 `c19_bonus: 5`，但個股版的 `scoring_weights` 完全沒有 c19，多頭買點信號（>60MA + KD金叉<20）在個股版不計分。

### 修改檔案

#### 2.1 `config.json` — scoring_weights 加入 c19_bonus
```json
"scoring_weights": {
    "c1": 10,
    "c2": 5,
    "c3": 5,
    "c4": 10,
    "c5": 5,
    "c6": 5,
    "c7": 10,
    "c9": 5,
    "c10": 5,
    "c11": 10,
    "c12": 5,
    "c13": 5,
    "c14": 5,
    "c15": 5,
    "c18": 10,
    "c19_bonus": 5
}
```

#### 2.2 `stock_screener.py` — `_DEFAULTS` 同步
```python
"scoring_weights": {
    "c1": 10, "c2": 5, "c3": 5, "c4": 10,
    "c5": 5, "c6": 5, "c7": 10, "c9": 5, "c10": 5,
    "c11": 10, "c12": 5, "c13": 5,
    "c14": 5, "c15": 5, "c18": 10,
    "c19_bonus": 5,    # 新增
},
```

#### 2.3 `common/scoring.py` — STOCK_SCORE_WEIGHTS 同步
```python
STOCK_SCORE_WEIGHTS = {
    # 技術面 (30 分)
    "c1": 10, "c2": 5, "c3": 5, "c4": 10,
    # 基本面 (30 分)
    "c5": 5, "c6": 5, "c7": 10, "c9": 5, "c10": 5,
    # 籌碼面 (20 分)
    "c11": 10, "c12": 5, "c13": 5,
    # 位階+攻擊 (20 分)
    "c14": 5, "c15": 5, "c18": 10,
    # C19 加分（非必須）
    "c19_bonus": 5,
}
```

#### 2.4 `stock_screener.py` — `_build_result` 中 conditions 加入 c19
目前已有：
```python
conditions = {
    ...
    "c19": pos.get("c19", False),
    ...
}
```
這部分已正確，c19 已在 conditions dict 中，只是沒有權重。加完權重即可生效。

#### 2.5 `stock_screener.py` — `print_summary` 的 label_map 加入 c19
```python
label_map = {
    ...
    "c18": "C18 攻擊訊號",
    "c19": "C19 多頭買點",    # 新增
}
```

### 影響評估
- 個股版總分上限從 100 變為 105（與 ETF 版一致），但 `calc_score` 有 `min(total, 100)` 保底
- 觸發 C19 的股票可多得 5 分，更容易進入 ENTER
- 不影響未觸發 C19 的股票得分

---

## 🟡 修改 3：C8 高股息填息自動化

### 問題
目前 `check_c8` 對高股息 ETF 只回傳 `manual: True`，完全不檢查填息狀態。高股息 ETF 最大風險是填息失敗，但目前形同虛設。

### 設計方案

利用 yfinance 的 dividend history + 價格歷史，自動計算「近 N 次除息後的填息天數」。

**填息定義**：除息日收盤價 ÷ 前一日收盤價 的跌幅，是否在 M 個交易日內被收復（即收盤價回到 ≥ 除息前一日收盤價）。

### 修改檔案

#### 3.1 `etf_screener.py` — 新增 `check_c8_auto` 函式

```python
def check_c8(type_label: str, ticker: str = "", df=None) -> dict:
    """C8 高股息填息檢查（自動化版）
    
    計算近 N 次除息後的填息狀態：
    - 近 3 次除息中至少 2 次在 30 個交易日內填息 → ok=True
    - 否則 → ok=False（填息失敗風險）
    """
    if type_label != "高股息":
        return {"ok": True, "detail": "C8=Y(不適用)"}
    
    c8_cfg = CONFIG["conditions"].get("C8", {})
    fill_days_max = c8_cfg.get("fill_days_max", 30)    # 填息最大容忍天數
    divs_to_check = c8_cfg.get("divs_to_check", 3)      # 檢查最近幾次除息
    min_fill_ratio = c8_cfg.get("min_fill_ratio", 0.67)  # 至少填息比例 (2/3)
    
    if df is None or not isinstance(df.index, pd.DatetimeIndex):
        return {"ok": True, "detail": "C8=⚠(無價格資料，需手動查填息)", "manual": True}
    
    try:
        ticker_yf = _RESOLVED_ETF_TICKERS.get(ticker) or f"{ticker}.TW"
        yf_ticker = yf.Ticker(ticker_yf)
        divs = yf_ticker.dividends
        if divs is None or divs.empty:
            return {"ok": True, "detail": "C8=⚠(無除息紀錄，需手動查)", "manual": True}
        
        # 取最近 N 次除息
        recent_divs = divs.sort_index().iloc[-divs_to_check:]
        
        close = df["close"]
        fill_results = []
        
        for div_date, div_amount in recent_divs.items():
            if div_date.tz is not None:
                div_date = div_date.tz_localize(None)
            
            # 找除息日在 df 中的位置
            div_loc = close.index.get_indexer([div_date], method='nearest')
            if len(div_loc) == 0 or div_loc[0] < 1:
                fill_results.append({"date": div_date.strftime("%Y-%m-%d"), "filled": None})
                continue
            
            idx = div_loc[0]
            # 除息前一日收盤（理論除息參考價 = 前收 - 息）
            pre_div_close = close.iloc[idx - 1]
            target_price = pre_div_close  # 填息目標 = 回到除息前收盤
            
            # 檢查後 fill_days_max 天內是否填息
            end_idx = min(idx + fill_days_max, len(close))
            filled = False
            fill_days = None
            for j in range(idx, end_idx):
                if close.iloc[j] >= target_price:
                    filled = True
                    fill_days = j - idx
                    break
            
            fill_results.append({
                "date": div_date.strftime("%m/%d"),
                "amount": round(div_amount, 2),
                "filled": filled,
                "fill_days": fill_days,
            })
        
        # 計算填息率
        valid_results = [r for r in fill_results if r["filled"] is not None]
        if not valid_results:
            return {"ok": True, "detail": "C8=⚠(無可計算除息資料)", "manual": True}
        
        fill_count = sum(1 for r in valid_results if r["filled"])
        fill_ratio = fill_count / len(valid_results)
        ok = fill_ratio >= min_fill_ratio
        
        # 組合明細
        details = []
        for r in valid_results:
            if r["filled"]:
                details.append(f"{r['date']}✅{r['fill_days']}d")
            else:
                details.append(f"{r['date']}❌未填")
        detail_str = f"C8={'Y' if ok else 'N'}(填息{fill_count}/{len(valid_results)}: {' '.join(details)})"
        
        return {"ok": ok, "detail": detail_str, "manual": False}
    
    except Exception as e:
        logger.warning("[%s] C8 填息檢查失敗: %s", ticker, e)
        return {"ok": True, "detail": "C8=⚠(填息檢查失敗，需手動查)", "manual": True}
```

#### 3.2 `config_etf.json` — C8 加入參數
```json
"C8": {
    "description": "高股息填息檢查：近3次除息中至少2次在30日內填息",
    "fill_days_max": 30,
    "divs_to_check": 3,
    "min_fill_ratio": 0.67
}
```

#### 3.3 `etf_screener.py` — `_DEFAULTS` 同步
```python
"C8": {"fill_days_max": 30, "divs_to_check": 3, "min_fill_ratio": 0.67},
```

#### 3.4 `etf_screener.py` — `screen_one` 修改呼叫
```python
# 原本：
c8 = check_c8(type_label)
# 改為：
c8 = check_c8(type_label, ticker, df)
```

#### 3.5 `_build_result` 中 c8_manual 判斷更新
原本：
```python
c8_manual = c8.get("manual", False)
```
不需修改，因為新函式在自動檢查成功時 `manual=False`，失敗時 `manual=True`。

### 影響評估
- 高股息 ETF 若近 3 次除息填息率 < 67%（如只填 1 次），C8 將判定為不合格
- 填息資料來自 yfinance，可能有 1~2 天延遲，對剛除息的 ETF 可能暫時無法判定
- 若 yfinance 無法取得除息資料，仍 fallback 到 `manual: True`

---

## 🟡 修改 4：C13 加入自營商判斷

### 問題
C13（散戶接手）目前只看外資+投信淨賣超+融資增，缺少自營商（dealer）。自營商大買+融資增也可能是散戶行為的替代信號。

### 設計方案

在 `get_chip` 中取得自營商買賣超資料（TWSE T86 報表第 16 欄位：自營商買賣超），在 `check_chip` 中加入自營商判斷邏輯。

**注意**：TWSE T86 的欄位結構為：
- [0] 證券代號
- [4] 外資買賣超
- [10] 投信買賣超  
- [16] 自營商買賣超（自行買賣）+ [17] 避險買賣超 → 取 [16]

### 修改檔案

#### 4.1 `stock_screener.py` + `etf_screener.py` — `get_chip` 加入自營商

```python
def get_chip(stock_id: str, df) -> dict:
    """三大法人買賣超 + 融資券（取近6個交易日）"""
    info = {"foreign_net": [], "trust_net": [], "dealer_net": [],  # 新增 dealer_net
            "margin_balance": [], "dates": [], "margin_dates": []}
    if df is None or not isinstance(df.index, pd.DatetimeIndex):
        return info

    for dt in df.index[-6:]:
        ds = dt.strftime("%Y%m%d")
        t86 = _t86_by_date(ds)
        if stock_id in t86:
            def p(i): return int(t86[stock_id][i].replace(",", ""))
            info["foreign_net"].append(p(4))
            info["trust_net"].append(p(10))
            info["dealer_net"].append(p(16))  # 新增：自營商買賣超
            info["dates"].append(ds)
        t93 = _t93_by_date(ds)
        if stock_id in t93:
            try:
                info["margin_balance"].append(int(t93[stock_id][6].replace(",", "")))
                info["margin_dates"].append(ds)
            except:
                info["margin_balance"].append(0)
                info["margin_dates"].append(ds)
    return info
```

#### 4.2 `stock_screener.py` + `etf_screener.py` — `check_chip` C13 判斷加入自營商

原本 C13 邏輯：
```python
recent_fnet2 = sum(tn[-ld:]) + sum(fn[-ld:])
if mb[-1] > mb[-ld] and recent_fnet2 < 0:
    c13 = True
```

修改為：
```python
# C13: 法人大賣但融資大增（含自營商）
ld = c13_cfg["lookback_days"]
dn = info.get("dealer_net", [])  # 自營商

# 三大法人合計賣超
has_enough_data = len(fn) >= ld and len(mb) >= ld and len(tn) >= ld
if has_enough_data:
    recent_fnet2 = sum(tn[-ld:]) + sum(fn[-ld:])
    # 加入自營商（若有資料）
    if len(dn) >= ld:
        recent_fnet2 += sum(dn[-ld:])
    
    if mb[-1] > mb[-ld] and recent_fnet2 < 0:
        c13 = True
        # 組合明細
        sellers = []
        if sum(fn[-ld:]) < 0: sellers.append(f"外資賣{sum(fn[-ld:]):,}")
        if sum(tn[-ld:]) < 0: sellers.append(f"投信賣{sum(tn[-ld:]):,}")
        if len(dn) >= ld and sum(dn[-ld:]) < 0: sellers.append(f"自營商賣{sum(dn[-ld:]):,}")
        c13_detail = f"融資增{mb[-1]-mb[-ld]:,}, {'+'.join(sellers)}"
```

同時更新 C13 的 **反向邏輯**（自營商大買 + 融資增也可能是散戶行為）：

```python
# 補充：自營商大買 + 融資大增 → 散戶透過自營商進場的信號
if not c13 and len(dn) >= ld and len(mb) >= ld:
    dealer_buy = sum(dn[-ld:])
    margin_increase = mb[-1] - mb[-ld]
    # 自營商買超 > 融資增額的 50% → 可能是散戶透過自營商避險帳戶進場
    if dealer_buy > 0 and margin_increase > 0 and dealer_buy > margin_increase * 0.5:
        c13 = True
        c13_detail = f"自營商買{dealer_buy:,}+融資增{margin_increase:,}(疑似散戶)"
```

### ⚠️ 欄位索引注意事項
TWSE T86 的自營商欄位位置需要確認。不同日期/版本的 API 格式可能不同。建議：
1. 先用 `dump_candidates.py` 或直接 curl T86 API 確認欄位結構
2. 如果 T86 回傳欄位數不足（len(row) <= 16），跳過自營商，保持向後兼容

```python
# 安全取值
try:
    dealer_val = p(16) if len(t86[stock_id]) > 16 else 0
except (IndexError, ValueError):
    dealer_val = 0
info["dealer_net"].append(dealer_val)
```

### 影響評估
- C13 的散戶接手判斷更完整，涵蓋三大法人
- 新增「自營商大買+融資增」的替代信號，可能多抓到一些風險標的
- 若 T86 無自營商欄位，自動 fallback 到原本邏輯，不影響穩定性

---

## 🟡 修改 5：槓桿/反向 ETF type_adjusted 門檻差異化

### 問題
`config_etf.json` 的 C14 和 C16 已有 `type_adjusted` 欄位且已有值（C14 槓桿型 proximity_max=3.0, C16 槓桿型 gain_max=80.0），但：
1. C14 槓桿型設 3.0% **太窄**（2 倍槓桿 ETF 日波動 2~3% 很常見，5 天內偏離 60MA 3% 就被淘汰不合理）
2. C15 沒有 type_adjusted（連 3 日上漲對槓桿型太容易觸發）
3. 程式碼中 C14 有讀取 `type_adjusted`，但 C16 的 `type_adjusted` **沒有在程式碼中被使用**

### 修改檔案

#### 5.1 `config_etf.json` — 調整 type_adjusted

```json
"C14": {
    "ma_period": 60,
    "proximity_min": 0.0,
    "proximity_max": 5.0,
    "type_adjusted": {
        "高股息": { "proximity_max": 8.0 },
        "槓桿/反向型": { "proximity_max": 10.0 }
    }
},
"C15": {
    "consecutive_up_days": 3,
    "type_adjusted": {
        "槓桿/反向型": { "relaxed_mode": true, "up_days_in_window": 3, "window_days": 5 }
    }
},
"C16": {
    "lookback_days": 40,
    "gain_max": 20.0,
    "type_adjusted": {
        "高股息": { "gain_max": 15.0 },
        "槓桿/反向型": { "gain_max": 80.0, "lookback_days": 20 }
    }
}
```

**C14 槓桿型 3.0→10.0 的理由**：
- 2 倍槓桿 ETF 追蹤指數 2 倍日報酬
- 指數漲 2.5% → ETF 漲 5%，5 天連漲偏離 60MA 可達 10%+
- 3% 門檻幾乎必定淘汰槓桿型，失去篩選意義
- 10% 是合理的「仍處於均線附近」範圍

**C15 槓桿型 relaxed_mode 的理由**：
- 槓桿型 ETF 日波動大，嚴格連 3 日上漲太苛刻
- relaxed_mode: 近 5 日中至少 3 日上漲

#### 5.2 `etf_screener.py` — `check_position` 中 C16 讀取 type_adjusted

目前 C16 區段：
```python
# C16: 近 N 日翻倍 (type-adjusted)
gain_max = c16_cfg["gain_max"]
ta_c16 = c16_cfg.get("type_adjusted", {})
if type_label in ta_c16:
    gain_max = ta_c16[type_label].get("gain_max", gain_max)
```

需增加 lookback_days 的 type_adjusted：
```python
# C16: 近 N 日翻倍 (type-adjusted)
gain_max = c16_cfg["gain_max"]
lookback_days_c16 = c16_cfg["lookback_days"]
ta_c16 = c16_cfg.get("type_adjusted", {})
if type_label in ta_c16:
    gain_max = ta_c16[type_label].get("gain_max", gain_max)
    lookback_days_c16 = ta_c16[type_label].get("lookback_days", lookback_days_c16)

look16 = min(lookback_days_c16, len(c))
gain_2m = (c[-1] / c[-look16] - 1) * 100
doubled = gain_2m >= gain_max
```

#### 5.3 `etf_screener.py` — `check_position` 中 C15 讀取 type_adjusted

目前 C15 區段：
```python
# C15: 連3日上漲
up_days = c15_cfg["consecutive_up_days"]
if len(c) >= up_days + 1:
    c15 = all(c[-(i+1)] > c[-(i+2)] for i in range(up_days))
else:
    c15 = False
```

修改為：
```python
# C15: 連續上漲 (type-adjusted)
ta_c15 = c15_cfg.get("type_adjusted", {})
if type_label in ta_c15 and ta_c15[type_label].get("relaxed_mode", False):
    # 寬鬆模式：近 window_days 日中至少 up_days_in_window 日上漲
    window = ta_c15[type_label].get("window_days", 5)
    min_up = ta_c15[type_label].get("up_days_in_window", 3)
    if len(c) >= window + 1:
        up_count = sum(1 for i in range(window) if c[-(i+1)] > c[-(i+2)])
        c15 = up_count >= min_up
    else:
        c15 = False
else:
    # 標準模式：連續 N 日上漲
    up_days = c15_cfg["consecutive_up_days"]
    if len(c) >= up_days + 1:
        c15 = all(c[-(i+1)] > c[-(i+2)] for i in range(up_days))
    else:
        c15 = False
```

### 影響評估
- 槓桿型 ETF 不再因 proximity_max 太窄而被系統性淘汰
- 槓桿型 C16 lookback_days=20（比原 40 短），因為槓桿型波動大，40 天前的價格參考價值低
- C15 relaxed_mode 降低假陰性，但保持至少 3/5 日上漲的門檻

---

## 🟢 修改 6：缺少量縮出場信號（E5）

### 問題
出場條件只有「破均線」（E1/E3）、「近高檔反轉+KD死叉」（E2）、「連跌4日+量縮」（E4）。缺少「量縮價穩」的出場信號——成交量持續低於均量，股價雖未大跌但量能退潮，可能是主力退場的前兆。

### 設計方案
新增 E5：連續 N 日成交量 < 20 日均量的 M%（預設 N=5, M=70%），且收盤價在 20MA 附近（±3%），視為量能退潮。

### 修改檔案

#### 6.1 `common/scoring.py` — `check_exit` 加入 E5

```python
def check_exit(df, pos: dict, params: dict = None) -> list[str]:
    if df is None or len(df) < 60:
        return []
    if params is None:
        params = DEFAULT_EXIT_PARAMS

    close = df["close"].to_numpy()
    volume = df["volume"].to_numpy()
    ma20 = pd.Series(close).rolling(params["ma20_period"]).mean().to_numpy()
    ma60 = pd.Series(close).rolling(params["ma60_period"]).mean().to_numpy()
    reasons = []

    # E1~E4 保持不變 ...

    # E5: 量能退潮 — 連續 N 日量縮 + 價格在月線附近
    e5_shrink_days = params.get("e5_shrink_days", 5)
    e5_vol_ratio = params.get("e5_volume_ratio_e5", 0.7)
    e5_price_range = params.get("e5_price_range", 0.03)  # ±3%
    if len(close) >= 20 + e5_shrink_days:
        vol_ma20 = pd.Series(volume).rolling(20).mean().to_numpy()
        shrink_count = 0
        for i in range(1, e5_shrink_days + 1):
            if not np.isnan(vol_ma20[-i]) and volume[-i] < vol_ma20[-i] * e5_vol_ratio:
                shrink_count += 1
        # 連續 N 日量縮
        if shrink_count >= e5_shrink_days:
            near_ma20 = not np.isnan(ma20[-1]) and abs(close[-1] - ma20[-1]) / ma20[-1] <= e5_price_range
            if near_ma20:
                reasons.append("E5: 量能退潮(連5日量<20MA70%+價貼月線)")

    return reasons
```

#### 6.2 `config.json` + `config_etf.json` — exit_params 加入 E5

```json
"exit_params": {
    "ma20_period": 20,
    "ma60_period": 60,
    "lookback_days_e1": 30,
    "lookback_days_e2": 40,
    "high_ratio_e2": 0.95,
    "volume_ratio_e4": 0.7,
    "e5_shrink_days": 5,
    "e5_volume_ratio_e5": 0.7,
    "e5_price_range": 0.03
}
```

#### 6.3 兩 screener 的 `_DEFAULTS` — 同步
```python
"exit_params": {
    "ma20_period": 20, "ma60_period": 60,
    "lookback_days_e1": 30, "lookback_days_e2": 40,
    "high_ratio_e2": 0.95, "volume_ratio_e4": 0.7,
    "e5_shrink_days": 5, "e5_volume_ratio_e5": 0.7, "e5_price_range": 0.03,
},
```

### 影響評估
- 新增量能退潮出場信號，補足目前只看「價格跌破」的盲區
- E5 條件較嚴格（連 5 日量縮 + 價格貼月線），不會產生太多假信號
- 對盤整量縮的標的可提前警示

---

## 🟢 修改 7：C14 設計討論 —「0~5% 範圍」vs「回測站回」

### 問題
C14 目前設計為「收盤價距 60MA 在 0~5% 範圍內」，這是一個「靠近均線=好的買點」的靜態判斷。而 C3 是「近 N 日 Low 曾跌破 20MA」，是「回測站回」的動態判斷。兩者邏輯不對齊。

### 討論（非強制修改）

**方案 A：維持現狀（0~5% 範圍）**
- 優點：簡單直觀，靠近均線=好買點
- 缺點：強勢股漲超過 5% 偏離 60MA 會被 C14 淘汰；無法捕捉「回測再上」的型態
- 適合：保守型投資人，只買均線附近的股票

**方案 B：改為回測站回邏輯**
```
C14: 近 N 日內曾跌破 60MA，但最新收盤站回 60MA 上方
```
- 優點：與 C3 邏輯一致，形成「月線回測」+「季線回測」雙確認
- 缺點：需要多一個參數（lookback_days），且可能漏掉「一直在均線上方緩漲」的股票
- 適合：趨勢追蹤型投資人，重視回測確認

**方案 C：混合模式（推薦）**
```
C14_pass = (距60MA 0~5%) OR (近N日曾破60MA但收盤站回)
```
- 優點：涵蓋兩種買點型態
- 缺點：通過率較高，可能需要調整權重或 ENTER 門檻補償
- 適合：通用型篩選器

### 如果選擇方案 C，修改如下

#### 7.1 `config.json` — C14 加入 lookback_days
```json
"C14": {
    "description": "60MA 回測買點：距60MA 0~5% OR 近N日曾破60MA但站回",
    "ma_period": 60,
    "proximity_min": 0.0,
    "proximity_max": 5.0,
    "lookback_days": 5,
    "mode": "hybrid"
}
```

#### 7.2 `stock_screener.py` — `check_position` C14 區段修改

```python
# C14: 60MA 回測買點
ma60 = pd.Series(c).rolling(c14_cfg["ma_period"]).mean().to_numpy()
if not np.isnan(ma60[-1]) and ma60[-1] > 0:
    dist_from_ma60 = (cur - ma60[-1]) / ma60[-1] * 100
    proximity_ok = c14_cfg["proximity_min"] <= dist_from_ma60 <= c14_cfg["proximity_max"]
    
    # 混合模式：也接受「回測站回」
    mode = c14_cfg.get("mode", "proximity")
    if mode == "hybrid":
        lookback = c14_cfg.get("lookback_days", 5)
        low_arr = df["low"].to_numpy() if "low" in df.columns else l
        pullback_ok = False
        if dist_from_ma60 >= 0:  # 目前在 60MA 上方
            for d in range(1, min(lookback + 1, len(low_arr))):
                if low_arr[-d] < ma60[-d] and not np.isnan(ma60[-d]):
                    pullback_ok = True
                    break
        c14 = proximity_ok or pullback_ok
    elif mode == "pullback":
        lookback = c14_cfg.get("lookback_days", 5)
        pullback_ok = False
        if dist_from_ma60 >= 0:
            for d in range(1, min(lookback + 1, len(l))):
                if l[-d] < ma60[-d] and not np.isnan(ma60[-d]):
                    pullback_ok = True
                    break
        c14 = pullback_ok
    else:
        c14 = proximity_ok
else:
    dist_from_ma60 = None
    c14 = False
```

#### 7.3 `etf_screener.py` — 同步修改（含 type_adjusted 兼容）
ETF 版的 C14 已有 type_adjusted，混合模式也應支援。

### 影響評估
- 混合模式會讓 C14 通過率提高，ENTER 級股票數量可能增加
- 如果 ENTER 增加太多，可考慮將 C14 權重從 5 降至 3，或將 ENTER 門檻從 75 提升至 80
- 建議先跑一次 A/B 測試：同一批候選股分別用 proximity/hybrid 模式跑，比較結果差異

---

## 實作優先順序建議

| 順序 | 修改 | 預估工時 | 風險 |
|------|------|---------|------|
| 1 | #1 C13 硬淘汰 | 15 min | 低（純 config + 預設值修改）|
| 2 | #2 C19 評分 | 15 min | 低（純 config + 權重修改）|
| 3 | #5 槓桿型 type_adjusted | 30 min | 中（需修改 check_position 邏輯）|
| 4 | #4 C13 自營商 | 1 hr | 中（需確認 T86 欄位索引 + 雙 screener 修改）|
| 5 | #3 C8 填息自動化 | 1.5 hr | 中（新函式 + yfinance dividend API 依賴）|
| 6 | #6 E5 量縮出場 | 30 min | 低（新增出場信號，不影響現有邏輯）|
| 7 | #7 C14 混合模式 | 1 hr | 中（需 A/B 測試驗證）|

**總工時估計：約 5 小時**

### 測試計畫
1. 每項修改完成後跑 `pytest tests/` 確認既有測試通過
2. 修改 #5（槓桿型）後，單獨跑一檔槓桿型 ETF（如 00631L）驗證 C14/C15/C16 門檻
3. 修改 #3（填息）後，跑 0056 或 00878 驗證填息計算正確
4. 修改 #4（自營商）後，確認 T86 API 回傳欄位數 > 16 才讀取自營商
5. 全部修改完成後，跑完整候選清單做回歸測試
