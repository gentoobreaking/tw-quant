# P0-2 位阶重新设计 C14/C15 对齐 60MA 回测买点

> 日期：2026-06-10

## 问题

旧 C14（距52週高點修正≥30%）+ C15（底部橫盤≥90日且波動<25%）是股灾抄底逻辑。
与 C1-C4（上升趋势）硬用 AND 连接，几乎不可能同时通过——要在「大跌30%」的同时「站上60MA且向上」，逻辑矛盾。

## 修复方案

### C14_new：60MA 回测买点

收盘价距 60MA 在 0~5% 范围内 → 股价接近或刚站回 60MA，是回测买点区域。

```python
ma60 = pd.Series(c).rolling(60).mean().to_numpy()
dist_from_ma60 = (cur - ma60[-1]) / ma60[-1] * 100
c14 = 0.0 <= dist_from_ma60 <= 5.0
```

**ETF 类型调整**：
- 高股息：proximity_max=8%（波动小，放宽）
- 槓桿/反向型：proximity_max=3%（波动大，收紧）
- 市值型：默认 5%

### C15_new：连3日上涨确认

近3个交易日收盘价连续上涨 → 回测后确认反转信号。

```python
c15 = all(c[-(i+1)] > c[-(i+2)] for i in range(3))
```

## 修改文件

| 文件 | 变更 |
|------|------|
| stock_screener.py | CONFIG C14/C15 参数、check_position 逻辑、detail 输出、print 行 |
| etf_screener.py | 同上 + ETF type_label 分群调整 proximity_max |
| improvement.md | P0-1/P0-2 标记 ✅ 已修复 |

## 新旧对比

| | 旧 C14 | 新 C14 |
|---|---|---|
| 含义 | 距52週高點跌≥30% | 收盘价距60MA 0~5% |
| 适合场景 | 股灾抄底 | 上升趋势回测买点 |
| 与 C1 兼容 | ❌ 几乎不可能同时通过 | ✅ 完全兼容 |

| | 旧 C15 | 新 C15 |
|---|---|---|
| 含义 | 底部横盘≥90日且波动<25% | 连3日收盘上涨 |
| 适合场景 | 长期底部确认 | 回测后反转确认 |
| 实用性 | 极端条件下才通过 | 正常行情即可通过 |

## 验证

- ✅ 两个文件 py_compile 编译通过
- ✅ 旧 C14(drawdown_min) / C15(range_period) 代码完全移除
- ✅ 新 C14(proximity_min/max) / C15(consecutive_up_days) 代码已到位
- ✅ ETF 保留 type_label 分群调整（高股息8%、槓桿3%）
- ✅ detail_parts 和 print 输出已更新
- ✅ improvement.md 标记 P0 已完成
