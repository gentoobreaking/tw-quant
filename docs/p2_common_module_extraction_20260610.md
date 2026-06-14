# P2-1 共用模块抽取

> 日期：2026-06-10

## 目标

两个 screener 有 ~60% 复制贴上（~575 行重复），抽取为 `common/` 套件。

## 产出

### common/ 套件 (9 个文件, ~600 行)

| 文件 | 职责 | 行数 |
|------|------|------|
| `__init__.py` | 统一导出 | 9 |
| `config.py` | 设定档载入 + deep merge | 25 |
| `cache.py` | SQLite 磁碟快取（取代单 JSON） | 130 |
| `rate_limit.py` | 多资料源 rate limiter | 32 |
| `twse.py` | TWSE API 呼叫 | 51 |
| `tdcc.py` | TDCC 集保查询（session + 快取） | 151 |
| `yf_utils.py` | yfinance 批次下载 + 序列化 | 123 |
| `kd.py` | KD 计算（支援 k_smooth/d_smooth） | 40 |
| `serialization.py` | numpy/pandas ↔ JSON 转换 | 32 |

### 关键改进

1. **SQLite 快取取代单 JSON**：解决 80MB+ 载入慢问题，逐 key 存取
2. **KD 统一**：`calc_kd()` 支援个股 (9,3,3) 和 ETF (20,5,5) 参数
3. **TDCC 封装为类**：session 管理 + 磁碟快取整合
4. **RateLimiter 类**：取代全域 mutable dict
5. **DiskCache 类**：取代 lambda 闭包陷阱 + 无大小上限问题

### 代码量变化

- 旧：stock_screener.py 1212L + etf_screener.py 1282L = **2494L**
- 新：stock_screener.py 784L + etf_screener.py 866L + common/ 600L = **2250L**
- 净减 **244 行**，且 common/ 可被其他工具复用

### 兼容性

- Python 3.9 兼容（使用 `Optional[X]` 而非 `X | None`）
- 外部配置档 (config.json / config_etf.json) 不变
- 候选股档 (candidates.csv / candidates_ETF.csv) 不变
- 快取目录 (.cache/ / .cache_etf/) 自动迁移至 SQLite (.db)
