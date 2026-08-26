"""T014 e2e 測試 — 斷網容錯、備援演練、報表產生（合成資料）"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import pipeline_screener as ps
import common.scorer as scorer_mod
from common.finmind import with_fallback


# ---- 斷網：stage0 失敗 → exit 1 ----
def test_main_returns_1_when_stage0_fails(tmp_path):
    cfg = ps.load_config()
    with patch.object(ps, "stage0_universe",
                      side_effect=RuntimeError("all network down")), \
         patch.object(ps, "DiskCache", MagicMock()):
        rc = ps.main(["--rebuild-universe"])
    assert rc == 1


def test_dry_run_no_network_exit0():
    assert ps.main(["--dry-run"]) == 0


# ---- 報表產生（合成資料，含六個章節）----
def test_write_reports_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(scorer_mod, "RESULT_DIR", tmp_path)
    full = pd.DataFrame([
        {"ticker": "2330", "name": "台積電", "sector": "電子工業", "total": 85,
         "f1": 15, "f2": 22, "f3": 18, "f4": 12, "f5": 8,
         "eps_2026": 107.6, "eps_2027": 142.1, "rev_1m": 0.93, "rev_3m": 9.84,
         "foreign_20d": 5000.0, "trust_20d": 800.0, "main_force_20d": 5800.0,
         "dist_60d_high": -2.0, "ma20": 98.0, "ma60": 90.0, "close": 100.0,
         "entry_low": 98.0, "entry_high": 100.94, "stop_loss": 91.14,
         "target_price": 130.0, "rr": 2.2},
    ])
    top10 = full.head(1)
    details = pd.DataFrame([{"ticker": "2330", "rev_1m": "+0.93%→9分"}])
    rejected = pd.DataFrame([{"ticker": "2454", "name": "聯發科",
                              "rejected_rules": "H1", "total": 60}])
    stats = {"primary": 40, "finmind": 8, "failures": 2}
    top5 = pd.DataFrame([{**full.iloc[0].to_dict(), "grade": "S",
                          "conclusion": "EPS上修＋法人轉買＋低位階＋2027成長，研究進場"}])

    from common.scorer import write_reports
    md = write_reports(full, top10, details,
                       rejected=rejected, stats=stats,
                       top5=top5, out_dir=tmp_path)
    text = open(md, encoding="utf-8").read()
    for section in ("一、Top5 買點清單", "二、表二：Top10 量化表",
                    "三、表一：全量量化表", "四、淘汰名單",
                    "訊號分級定義",
                    "五、資料源統計", "七、欄位計算說明"):
        assert section in text, f"缺章節 {section}"
    assert "FinMind 備援啟用：8 次" in text
    assert "H1：" in text                    # 淘汰規則圖例（人類可讀）
    assert "規則" in text                     # 淘汰表格含說明欄
    assert "H1：2027 EPS 預估負成長" in text  # 圖例渲染
    assert "S 級：" in text                   # 分級定義圖例
    assert "7-B" in text                     # 子項數值附錄

    # 新輸出：Parquet + SQLite + summary.md（不再有 CSV）
    assert (tmp_path / "pipeline_20260826_full.parquet").exists()
    assert (tmp_path / "pipeline_20260826_top10.parquet").exists()
    assert (tmp_path / "pipeline_20260826_top5.parquet").exists()
    assert (tmp_path / "screening_history.db").exists()
    assert (tmp_path / "pipeline_20260826_summary.md").exists()

# ---- FinMind 備援演練：主路徑失敗 → 統計計數 ----
def test_fallback_stats_counted():
    from common import finmind as fm
    fm.reset_stats()
    data, source = with_fallback(lambda: (_ for _ in ()).throw(RuntimeError("x")),
                                 lambda: [{"ok": 1}], label="測")
    st = fm.get_stats()
    assert source == "finmind" and st["finmind"] == 1 and st["failures"] == 0
