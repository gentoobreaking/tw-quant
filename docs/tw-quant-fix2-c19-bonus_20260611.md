# TW-Quant 修改 2：C19 多頭買點計入個股版評分

**日期**: 2026-06-11
**狀態**: ✅ 完成

## 修改內容

個股版 scoring_weights 新增 `c19_bonus: 5`，與 ETF 版一致。多頭買點（>60MA + KD金叉<20）在個股版不再形同虛設。

## 改動檔案

| 檔案 | 改動 |
|------|------|
| `config.json` | `scoring_weights` 加入 `"c19_bonus": 5` |
| `stock_screener.py` | `_DEFAULTS["scoring_weights"]` 加入 `"c19_bonus": 5` |
| `stock_screener.py` | `print_summary` label_map 加入 `"c19": "C19 多頭買點"` |
| `common/scoring.py` | `STOCK_SCORE_WEIGHTS` 加入 `"c19_bonus": 5` |

## 計分邏輯

- `c19_bonus` 是加分項：c19 通過才加 5 分，不通過不扣
- 個股版總分上限從 100 變為 105，但 `calc_score` 有 `min(total, 100)` 封頂
- C19 的實際價值：當其他條件有缺時，C19 可補 5 分幫助跨過 ENTER 門檻（75分）
- 無需修改 `_build_result`（c19 已在 conditions dict 中）或 `calc_score`（已支援 _bonus 後綴）

## 測試結果

- pytest 43/43 全通 ✅
- 手動驗證：C14+C15未過 + C19觸發 → 95分（比無C19的90分多5分）✅
- config.json 載入驗證通過 ✅
