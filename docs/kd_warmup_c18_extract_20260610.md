# KD Warmup + C18 抽出

> 日期：2026-06-10

## 目标

1. KD 计算前 N 根受初始值 50 影响不具参考意义，需标为 NaN（warmup）
2. C18 攻击讯号的 60 分线下载逻辑嵌在 `check_position()` 中，需抽出独立函数

## 实作

### 1. KD Warmup (`common/kd.py`)

- `calc_kd()` 新增 `warmup=True` 参数（预设开）
- warmup 区间 = `period * 2`：KD(9,3,3) → 前 18 根 NaN，KD(20,5,5) → 前 40 根 NaN
- 此区间内 K/D 值仍受初始值 50 影响，标为 NaN 避免误判黄金/死亡交叉

### 2. C18 抽出 (`check_c18_attack()`)

- 60 分线下载 + 快取 + 黄金交叉判定 → 独立函数
- 日线带量红K 回退逻辑也纳入
- `check_position()` 改为 `c18, c18_detail = check_c18_attack(df, ticker_yf, c18_cfg)`
- 两个 screener 各有一份 `check_c18_attack()`（因共用 `cache`/`rate_limiter` 模组变数不同）

## 验证

- KD(9,3,3) warmup: 前 18 根 NaN ✅
- KD(20,5,5) warmup: 前 40 根 NaN ✅
- warmup=False: 0 根 NaN ✅
- 两个 screener 语法 + import 正常 ✅
