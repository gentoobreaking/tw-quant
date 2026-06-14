# P1-3 ETF C5 溢价门坎放宽 + 币别处理 + 折价加分

> 日期：2026-06-10

## 问题

1. 溢价门坎 0.5% 过严，除权息前后几乎必触发
2. 海外 ETF（如美股 ETF）币别非 TWD，溢价计算无意义却仍检查
3. 折价（负溢价）是套利机会，但原逻辑只是「通过」，没有加分

## 修复 (etf_screener.py)

1. **门坎放宽**: CONFIG `premium_max` 从 0.5 → 3.0
2. **币别检查**: 新增 `info.get("currency")` 检查，非 TWD 跳过溢价检查，返回 ok=True
3. **折价加分**: `premium_pct < 0` 时直接 ok=True 并标注 👍
4. **detail 改善**: 显示当前门坎值，方便调试

## 不影响

- stock_screener.py 无 C5 溢价条件
