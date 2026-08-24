"""T012 單元測試 — 硬淘汰 H1–H5（每條規則正反例＋無覆蓋案例）"""
import pandas as pd
import pytest

from common.grading import apply_hard_rejects


def base_row(**kw):
    row = {
        "ticker": "2000", "total": 70, "growth_2027": 0.2,
        "rev_3m": 3.0, "foreign_20d": 100.0, "trust_20d": 50.0,
        "growth_2026": 0.30, "rev_yoy_3m": 5.0,
        "dist_60d_high": -10.0, "rsi14": 55, "f2": 20, "rr": 1.8,
    }
    row.update(kw)
    return row


def run(rows):
    df = pd.DataFrame([base_row(**r) for r in rows])
    passed, rejected = apply_hard_rejects(df)
    return passed, rejected


def test_h1_positive_and_negative():
    p, r = run([base_row(growth_2027=-0.05), base_row(ticker="2001")])
    assert "2000" in r.ticker.values and "H1" in r.iloc[0].rejected_rules
    assert "2001" in p.ticker.values


def test_h1_missing_data_skipped():
    p, r = run([base_row(growth_2027=None)])
    assert len(p) == 1
    assert "H1未檢" in p.iloc[0].rejected_rules


def test_h2_downgrade_over_threshold():
    p, r = run([base_row(rev_3m=-6.0), base_row(ticker="2001", rev_3m=-4.9)])
    assert list(r.ticker) == ["2000"]
    assert "H2" in r.iloc[0].rejected_rules
    assert "2001" in p.ticker.values


def test_h2_missing_data_skipped():
    p, r = run([base_row(rev_3m=None)])
    assert len(p) == 1 and "H2未檢" in p.iloc[0].rejected_rules


def test_h3_both_selling():
    p, r = run([base_row(foreign_20d=-500.0, trust_20d=-300.0),
                base_row(ticker="2001", foreign_20d=100.0, trust_20d=-50.0)])
    assert list(r.ticker) == ["2000"]
    assert "2001" in p.ticker.values   # 只有一方賣超不淘汰


def test_h4_no_growth_theme_only():
    p, r = run([base_row(growth_2026=0.0, rev_yoy_3m=0.0),
                base_row(ticker="2001", growth_2026=0.1, rev_yoy_3m=0.0)])
    assert list(r.ticker) == ["2000"]      # 雙零 → 淘汰
    # 一正一負不淘汰
    p2, r2 = run([base_row(growth_2026=0.1, rev_yoy_3m=-1.0)])
    assert len(p2) == 1


def test_h5_deducts_score_but_keeps():
    rows = [base_row(dist_60d_high=-1.5, rsi14=75, total=80),
            base_row(ticker="2001")]
    p, r = run(rows)
    assert len(r) == 0                     # H5 不淘汰
    h5row = p[p.ticker == "2000"].iloc[0]
    assert h5row["total"] == 70            # −10
    assert "H5降分" in h5row.rejected_rules
    # RSI 未過熱 → 不扣
    assert p[p.ticker == "2001"].iloc[0]["total"] == 70


def test_multiple_rules_accumulate():
    p, r = run([base_row(growth_2027=-0.1, rev_3m=-10.0)])
    assert "H1" in r.iloc[0].rejected_rules and "H2" in r.iloc[0].rejected_rules


def test_empty_frame():
    p, r = apply_hard_rejects(pd.DataFrame())
    assert p.empty and r.empty


# ---- T013：S/A/B 分級與 Top5 ----
from common.grading import build_top5, grade_signals   # noqa: E402


def grade_one(**kw):
    row = base_row(**kw)
    df = pd.DataFrame([row])
    return grade_signals(df, {"s": 2.0, "a": 1.5}).iloc[0]["grade"]


def test_grade_s():
    g = grade_one(rr=2.4, f2=27, f3=15, growth_2027=0.25)
    assert g == "S"


def test_grade_a_institution_not_yet():
    g = grade_one(rr=1.8, f2=20, f3=8, growth_2027=0.18)
    assert g == "A"


def test_grade_b_fallback():
    # R/R<1.5 強制 B（即使基本面好）
    g = grade_one(rr=1.2, f2=28, f3=19, growth_2027=0.3)
    assert g == "B"


def test_grade_c_when_below_60():
    # 條件全不達標（f2<18 且 R/R<1.5 且 total<60）→ C
    g = grade_one(total=40, f2=10, rr=1.0)
    assert g == "C"


def test_build_top5_order_and_conclusion():
    rows = [
        base_row(ticker="2000", rr=2.4, f2=27, f3=15),          # S
        base_row(ticker="2001", rr=1.8, f2=20, f3=8),           # A
        base_row(ticker="2002", rr=1.0, total=75),              # B
        base_row(ticker="2003", total=50),                      # C
    ]
    df = pd.DataFrame(rows)
    graded = grade_signals(df, {"s": 2.0, "a": 1.5})
    top5 = build_top5(graded)
    assert list(top5.ticker) == ["2000", "2001", "2002", "2003"]
    assert top5.iloc[0]["conclusion"].startswith("EPS上修＋法人轉買")
    assert top5.iloc[3]["conclusion"] == "條件不足，暫不列入"
