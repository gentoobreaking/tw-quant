# 合并 improvement.md 与 logic_improve.md

> 日期：2026-06-10

## 目标

将三份审查文件合并为一份统一的改善计划：
1. `improvement.md`（工程面 6 大项，~500 行）
2. `logic_improve.md`（逻辑面 18 大项，~800 行）
3. `tw_quant_review_20260610.md`（补充审查，新增 C11 bug 等）

## 执行结果

- ✅ **`improvement.md`** 已更新为完整合并版（28KB），按 P0→P4 优先级重组所有问题
- ✅ **`logic_improve.md`** 改为重定向说明，指向 improvement.md

## 合并策略

1. **去重**：三份文件有大量重叠（如 C8 冗余在两份中都出现、C14/C15 重新设计在 logic_improve 中详述而 review 中引用），合并时保留最完整的版本
2. **统一优先级**：原 improvement.md 用 P0-P5、logic_improve.md 用 P0-P4 但排序不同。合并后统一为 P0-P4，以问题严重度排序
3. **新增发现**：review 中新发现的 C11 对齐 bug、KD warmup、C3 lookback、快取 lambda 闭包等全部纳入

## 关键变更

| 原始位置 | 合并后位置 | 变更说明 |
|---------|-----------|---------|
| improvement.md §1 共用模块 | P2-1 | 保留，新增 SQLite 快取改进 |
| improvement.md §2 结果持久化 | P3-3 | 保留 |
| improvement.md §3 Logging | P3-3 | 保留，附属于共用模块 |
| improvement.md §4 单元测试 | P4-2 | 保留 |
| improvement.md §5 型别提示 | P4-1 | 保留 |
| improvement.md §6 代码品质 | 分散至各处 | C5/C6/C7 等分别归入 P1/P3 |
| logic_improve.md §1 C8 冗余 | P1-1 | 保留 |
| logic_improve.md §2 技术/位阶冲突 | P0-2 | 解法融入位阶重新设计 |
| logic_improve.md §3 基本面门坎 | P2-3 | 保留 |
| logic_improve.md §4 C14 门坎 | P0-2 | 融入位阶重新设计 |
| logic_improve.md §5 ETF C6 | P3-2 | 保留 |
| logic_improve.md §6 ETF C5 溢价 | P1-3 | 保留，新增折价加分 |
| logic_improve.md §7 KD 参数 | P1-6 | 保留，新增 warmup 问题 |
| logic_improve.md §8 C15 分母 | P1-4 | 保留 |
| logic_improve.md §9 ETF 操作建议 | P4-3 | 降级，长期优化 |
| logic_improve.md §10 得分制 | P2-2 | 保留 |
| logic_improve.md §11 C16 矛盾 | P3-1 | 保留 |
| logic_improve.md §12 ETF C7/C8 | P1-5 | 保留 |
| logic_improve.md §13 效能 | P4-3 | 保留 |
| logic_improve.md §15 C14/C15 重设 | P0-2 | 保留，为核心改动 |
| logic_improve.md §16 对照表 | 附录 | 保留 |
| logic_improve.md §17 模拟输出 | 附录 | 保留 |
| logic_improve.md §18 三層输出 | P2-2 | 融入得分制 |
| review C11 对齐 bug | P0-1 | **新增**，最严重逻辑 bug |
| review C3 lookback | P1-2 | **新增** |
| review KD warmup | P1-6 | **新增**，附属于 KD 改善 |
| review 快取问题 | P2-1 | **新增**，附属于共用模块 |

## 文件路径

- 合并后完整版：`/Users/claw/Projects/tw-quant/improvement.md`
- 重定向文件：`/Users/claw/Projects/tw-quant/logic_improve.md`
