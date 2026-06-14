# TW-Quant 修改 5：槓桿/反向 ETF type_adjusted 差異化門檻

**日期**: 2026-06-12
**狀態**: ✅ 完成

## 問題

槓桿/反向型 ETF 波動遠大於一般 ETF，但 C14/C15/C16 用同一套門檻，導致：
- C14 proximity_max=3% 太窄，槓桿型正常偏離 3-8% 都被判出局
- C15 要求「連3日漲」對槓桿型太嚴格（隔日回檔是常態）
- C16 lookback_days=40 天太長，槓桿型 20 天就能翻倍

## 修改內容

### C14 — 距60MA 門檻
| 類型 | proximity_max |
|------|-------------|
| 市值型（預設） | 5% |
| 高股息 | 8% |
| 槓桿/反向型 | **10%**（原 3% → 10%） |

### C15 — 連續上漲
| 類型 | 模式 |
|------|------|
| 市值型（預設） | 標準：連3日上漲 |
| 槓桿/反向型 | **寬鬆模式**：近5日至少3日上漲 |

### C16 — 翻倍門檻
| 類型 | gain_max | lookback_days |
|------|----------|--------------|
| 市值型（預設） | 20% | 40 |
| 高股息 | 15% | 40 |
| 槓桿/反向型 | 80% | **20**（新增） |

## 改動檔案

| 檔案 | 改動 |
|------|------|
| `config_etf.json` | C14 proximity_max 3→10, C15 新增 type_adjusted, C16 新增 lookback_days |
| `etf_screener.py` `_DEFAULTS` | 同步更新預設值 |
| `etf_screener.py` `check_position` | C15 新增 relaxed_mode 分支; C16 讀取 type_adjusted.lookback_days |

## C15 寬鬆模式邏輯

```python
ta_c15 = c15_cfg.get("type_adjusted", {})
if type_label in ta_c15 and ta_c15[type_label].get("relaxed_mode", False):
    # 近 window_days 日中至少 up_days_in_window 日上漲
    window = ta_c15[type_label].get("window_days", 5)
    min_up = ta_c15[type_label].get("up_days_in_window", 3)
    up_count = sum(1 for i in range(window) if c[-(i+1)] > c[-(i+2)])
    c15 = up_count >= min_up
else:
    # 標準模式：連續 N 日上漲
```

## 測試結果

- pytest 43/43 全通 ✅
- 槓桿型 C14 proximity_max=10% 生效 ✅
- 槓桿型 C15 寬鬆模式「近5日3漲」過關，市值型「連3日漲」未過 ✅
- 槓桿型 C16 gain_max=80%, lookback_days=20 生效 ✅
