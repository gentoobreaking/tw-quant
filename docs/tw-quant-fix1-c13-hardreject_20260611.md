# TW-Quant 修改 1：C13 散戶接加入硬淘汰

**日期**: 2026-06-11
**狀態**: ✅ 完成

## 修改內容

將 C13（散戶接手：法人大賣+融資增）從單純扣分項升級為硬淘汰條件，與 C20（空頭賣點）嚴重度對齊。

## 改動檔案

| 檔案 | 改動 |
|------|------|
| `common/scoring.py` | `DEFAULT_HARD_REJECT_RULES` 加入 `"c13": False` |
| `config.json` | `hard_reject_rules` 加入 `"c13": false` |
| `config_etf.json` | `hard_reject_rules` 加入 `"c13": false` |
| `stock_screener.py` | `_DEFAULTS["hard_reject_rules"]` 加入 `"c13": False` |
| `etf_screener.py` | `_DEFAULTS["hard_reject_rules"]` 加入 `"c13": False` |

## 邏輯說明

- `conditions["c13"] = True` → 非散戶接手（安全），不觸發淘汰
- `conditions["c13"] = False` → 散戶接手，觸發硬淘汰
- `rules["c13"] = False` → 當 c13==False 時匹配規則，觸發淘汰 ✅
- 現有 `check_hard_reject` 函式不需修改，邏輯為 `conditions.get(key) == expected`

## 測試結果

- pytest 43/43 全通
- 手動驗證：c13=False → hard_rejected=True, c13=True → hard_rejected=False ✅
- config.json / config_etf.json 載入驗證通過 ✅
