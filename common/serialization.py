"""序列化輔助 — numpy/pandas ↔ JSON 轉換"""
from typing import Optional

import numpy as np
import pandas as pd


def to_json_val(v):
    """將 numpy/pandas 值轉為 JSON-safe 原生類型"""
    if isinstance(v, (np.floating, float)):
        if np.isnan(v) or np.isinf(v):
            return None
        return float(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if v is None or isinstance(v, (str, int, float)):
        return v
    try:
        s = str(v)
        return s if s != "nan" and s != "NaT" else None
    except Exception:
        return None


def df_to_dict(df: pd.DataFrame) -> dict:
    """DataFrame → {idx: {col: val}} 供 JSON 序列化 (NaN→None)，保留原始 index"""
    idx_col = "___idx___"
    return {str(i): {idx_col: str(idx), **{c: to_json_val(row[c]) for c in df.columns}}
            for i, (idx, row) in enumerate(df.iterrows())}


def dict_to_df(data: dict) -> Optional[pd.DataFrame]:
    """還原 DataFrame，嘗試恢復 datetime index"""
    if not data:
        return None
    idx_col = "___idx___"
    rows = []
    dates = []
    for i in range(len(data)):
        row = data[str(i)].copy()
        dates.append(row.pop(idx_col, None))
        rows.append(row)
    df = pd.DataFrame(rows)
    if dates and any(d is not None for d in dates):
        try:
            parsed = pd.to_datetime(dates, errors="coerce")
            if parsed.notna().sum() > len(parsed) // 2:
                df.index = parsed
        except Exception:
            pass
    return df
