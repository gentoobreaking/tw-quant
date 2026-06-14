# P1-1 C8 冗余移除

> 日期：2026-06-10

## 问题

stock_screener.py 中 C6 检查 `负债比 < 50%`，C8 检查 `负债比 < 60%`。
C6 通过 → C8 必然通过，C8 零筛选力，纯粹冗余。

## 修复

从 stock_screener.py 中移除 C8：
- CONFIG 中 `C8` 键改为注释说明
- `check_fund()` 移除 `c8_cfg`、`c8` 变量及 `c8 and` 在 ok 链中
- 返回 dict 移除 `"c8": c8`
- print 区块移除 C8 行
- 顶部文档 21条件 → 20条件

**ETF 不受影响** — ETF 的 C8 是「高股息填息」，完全不同的条件。

## 验证

- ✅ py_compile 通过
- ✅ `c8`、`c8_cfg`、`c8_c` 变量完全移除
- ✅ `fund["ok"]` 不再依赖 c8
