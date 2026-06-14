import pytest
import sys
sys.path.insert(0, ".")

from common.scoring import (
    calc_score, check_hard_reject, classify_tier, check_exit,
    STOCK_SCORE_WEIGHTS, ETF_SCORE_WEIGHTS,
    DEFAULT_TIER_THRESHOLDS, DEFAULT_HARD_REJECT_RULES, DEFAULT_EXIT_PARAMS,
    TIER_ENTER, TIER_WATCH, TIER_EXIT, TIER_OUT,
)
import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════════════════
# calc_score
# ═══════════════════════════════════════════════════════════

class TestCalcScore:
    def test_all_passed(self):
        c = {"c1": True, "c2": True, "c3": True}
        w = {"c1": 10, "c2": 5, "c3": 5}
        assert calc_score(c, w) == 20

    def test_some_failed(self):
        c = {"c1": True, "c2": False, "c3": True}
        w = {"c1": 10, "c2": 5, "c3": 5}
        assert calc_score(c, w) == 15

    def test_all_failed(self):
        c = {"c1": False, "c2": False}
        w = {"c1": 10, "c2": 5}
        assert calc_score(c, w) == 0

    def test_empty_conditions(self):
        assert calc_score({}, {"c1": 10}) == 0

    def test_empty_weights(self):
        assert calc_score({"c1": True}, {}) == 0

    def test_bonus_applies(self):
        c = {"c19": True}
        w = {"c19_bonus": 5}
        assert calc_score(c, w) == 5

    def test_bonus_skipped_when_false(self):
        c = {"c19": False}
        w = {"c19_bonus": 5}
        assert calc_score(c, w) == 0

    def test_capped_at_100(self):
        c = {"c1": True, "c2": True, "c3": True}
        w = {"c1": 60, "c2": 60, "c3": 60}
        assert calc_score(c, w) == 100

    def test_stock_weights_sum_100(self):
        total = sum(v for k, v in STOCK_SCORE_WEIGHTS.items() if not k.endswith("_bonus"))
        assert total == 100

    def test_etf_weights_sum_100(self):
        base = sum(v for k, v in ETF_SCORE_WEIGHTS.items() if not k.endswith("_bonus"))
        bonus = sum(v for k, v in ETF_SCORE_WEIGHTS.items() if k.endswith("_bonus"))
        assert base == 95
        assert base + bonus == 100


# ═══════════════════════════════════════════════════════════
# check_hard_reject
# ═══════════════════════════════════════════════════════════

class TestCheckHardReject:
    def test_default_rules_trigger(self):
        assert check_hard_reject({"c16": False, "c20": True}) is True

    def test_default_rules_not_trigger(self):
        assert check_hard_reject({"c16": True, "c20": False}) is False

    def test_custom_rules(self):
        rules = {"c1": False}
        assert check_hard_reject({"c1": False}, rules) is True
        assert check_hard_reject({"c1": True}, rules) is False

    def test_missing_key_safe(self):
        assert check_hard_reject({}, {"c99": True}) is False

    def test_no_rules_default_used(self):
        assert check_hard_reject({}) is False


# ═══════════════════════════════════════════════════════════
# classify_tier
# ═══════════════════════════════════════════════════════════

class TestClassifyTier:
    def test_enter(self):
        assert classify_tier(80, False, c1=True, c14=True, c15=True) == TIER_ENTER

    def test_watch_score_barely(self):
        assert classify_tier(40, False, c1=False, c14=False, c15=False) == TIER_WATCH

    def test_out_score_too_low(self):
        assert classify_tier(39, False, c1=True, c14=True, c15=True) == TIER_OUT

    def test_out_hard_rejected(self):
        assert classify_tier(90, True, c1=True, c14=True, c15=True) == TIER_OUT

    def test_exit_signal_overrides(self):
        assert classify_tier(90, False, c1=True, c14=True, c15=True,
                              exit_signals=["E1"]) == TIER_EXIT

    def test_enter_needs_all_core(self):
        assert classify_tier(80, False, c1=True, c14=False, c15=True) == TIER_WATCH
        assert classify_tier(80, False, c1=False, c14=True, c15=True) == TIER_WATCH

    def test_custom_thresholds(self):
        th = {"enter_min": 50, "watch_min": 30}
        assert classify_tier(55, False, c1=True, c14=True, c15=True, thresholds=th) == TIER_ENTER

    def test_custom_thresholds_watch(self):
        th = {"enter_min": 60, "watch_min": 30}
        assert classify_tier(55, False, c1=False, c14=True, c15=True, thresholds=th) == TIER_WATCH


# ═══════════════════════════════════════════════════════════
# check_exit
# ═══════════════════════════════════════════════════════════

class TestCheckExit:
    @pytest.fixture
    def df_flat(self):
        dates = pd.date_range("2025-01-01", periods=200, freq="D")
        close = np.linspace(100, 105, 200)
        close[-1] = 95
        return pd.DataFrame({
            "close": close, "volume": np.ones(200) * 1e6,
            "open": close - 1, "high": close + 2, "low": close - 2,
        }, index=dates)

    def test_none_df(self):
        assert check_exit(None, {}) == []

    def test_short_df(self):
        df = pd.DataFrame({"close": [100]*10, "volume": [1]*10})
        assert check_exit(df, {}) == []

    def test_e3_跌破季線(self, df_flat):
        pos = {"c20": False}
        reasons = check_exit(df_flat, pos)
        assert any("E3" in r for r in reasons)

    @pytest.fixture
    def df_e2(self):
        dates = pd.date_range("2025-01-01", periods=200, freq="D")
        close = np.ones(200) * 100.0
        close[170:186] = 110.0
        close[186:199] = 107.0
        close[-1] = 85.0
        return pd.DataFrame({
            "close": close, "volume": np.ones(200) * 1e6,
            "open": close - 1, "high": close + 2, "low": close - 2,
        }, index=dates)

    def test_e2_高檔反轉(self, df_e2):
        pos = {"c20": True}
        reasons = check_exit(df_e2, pos)
        assert any("E2" in r for r in reasons), f"Got {reasons}"

    def test_custom_params(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="D")
        close = np.linspace(100, 110, 100)
        close[-1] = 90
        df = pd.DataFrame({"close": close, "volume": np.ones(100)*1e6})
        params = {"ma20_period": 10, "ma60_period": 20,
                  "lookback_days_e1": 20, "lookback_days_e2": 30,
                  "high_ratio_e2": 0.9, "volume_ratio_e4": 0.5}
        reasons = check_exit(df, {}, params=params)
        assert isinstance(reasons, list)
