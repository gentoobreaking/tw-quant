# P0-1 C11 筹码对齐 bug 修复

> 日期：2026-06-10

## 问题

`get_chip()` 返回 `foreign_net` / `trust_net` / `margin_balance` 三个扁平列表，没有日期键。
`check_chip()` 用 `idx_start = max(0, len(fn) - len(below_ma) - 5)` 推算 fn 起始位置，
但 fn 的日期（近6个交易日法人资料）与 below_ma 的日期（近N日跌破20MA的交易日）完全不同。

**后果**：程序以为 fn_seg 对应跌破期间的外资买卖超，实际上对不上——C11 的判断基础是错误的。

## 修复方案

### 1. `get_chip()` — 保留日期信息

新增 `dates`（法人资料日期，与 foreign_net/trust_net 对齐）和 `margin_dates`（融资资料日期，与 margin_balance 对齐）。

只有 TWSE API 有回传资料时才 append 对应日期，确保 dates 与 foreign_net 长度一致。

### 2. `check_chip()` — 按日期 join

- 将跌破 MA20 的日期转为 `YYYYMMDD` 字符串集合
- 从 chip_dates 中筛出对应日期的 fn/tn 值
- 若跌破期间无筹码资料（法人资料延迟），扩展到跌破日区间范围
- 移除旧的 `idx_start = max(0, len(fn) - len(below_ma) - 5)` 逻辑

## 修改文件

| 文件 | 函数 | 变更 |
|------|------|------|
| stock_screener.py | `get_chip()` | 新增 `dates`, `margin_dates` 字段 |
| stock_screener.py | `check_chip()` | 改用日期 join 取代长度推算 |
| etf_screener.py | `get_chip()` | 同上 |
| etf_screener.py | `check_chip()` | 同上 |

## 验证

- ✅ 两个文件 `py_compile` 编译通过
- ✅ `get_chip` 输出包含 `dates` 和 `margin_dates`
- ✅ `check_chip` 不再包含 `idx_start`（旧 bug 代码已移除）
- ✅ `check_chip` 包含 `below_dates_set`（新的日期对齐逻辑）

## C13 不受影响

C13 用 `fn[-ld:]` 和 `mb[-ld]` 检查「最近 ld 天」的法人卖超+融资增，是纯「最近 N 日」逻辑，不存在日期对齐问题，无需修改。
