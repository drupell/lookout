"""Tests for src/agent/market_signal_math.py — pure-function math, no I/O."""

from __future__ import annotations

from datetime import date

import pytest

from src.agent.market_signal_math import (
    FACTOR_WEIGHTS,
    LEAN_NOT_YET_THRESHOLD,
    LEAN_NOW_THRESHOLD,
    calendar_pressure_contribution,
    composite_from_factors,
    relative_delta_contribution,
)

# ---- calendar_pressure_contribution ----


class TestCalendarPressure:
    def test_mid_month_returns_zero(self):
        # Day 10 of June — no EOM ramp engaged.
        assert calendar_pressure_contribution(date(2026, 6, 10)) == 0.0

    def test_last_day_of_month_peaks(self):
        # June 30 — peak EOM AND end of Q2.
        contribution = calendar_pressure_contribution(date(2026, 6, 30))
        # Peak EOM gives factor=1.0; Q2-end bonus multiplies by 1.4.
        # Final value caps at the factor max (20.0).
        assert contribution == FACTOR_WEIGHTS["calendar_pressure"]

    def test_last_day_of_year_stacks_year_end_bonus(self):
        # Dec 31 stacks EOM peak + EOQ bonus + year-end bonus — caps at max.
        contribution = calendar_pressure_contribution(date(2026, 12, 31))
        assert contribution == FACTOR_WEIGHTS["calendar_pressure"]

    def test_ramp_between_d7_and_d3(self):
        # February 26 — 2 days to end-of-month (Feb has 28 days in 2026).
        # That's inside the peak window (<= 3 days).
        contribution = calendar_pressure_contribution(date(2026, 2, 26))
        # Inside peak window → eom_factor = 1.0, no Q/year bonuses in Feb.
        assert contribution == pytest.approx(FACTOR_WEIGHTS["calendar_pressure"])

    def test_ramp_at_d5(self):
        # November 25 — 5 days to end-of-month (Nov has 30).
        # eom_factor = (7 - 5) / (7 - 3) = 0.5. No quarter or year-end.
        contribution = calendar_pressure_contribution(date(2026, 11, 25))
        expected = FACTOR_WEIGHTS["calendar_pressure"] * 0.5
        assert contribution == pytest.approx(expected)

    def test_ramp_at_d6(self):
        # November 24 — 6 days to EOM.
        # eom_factor = (7 - 6) / (7 - 3) = 0.25.
        contribution = calendar_pressure_contribution(date(2026, 11, 24))
        expected = FACTOR_WEIGHTS["calendar_pressure"] * 0.25
        assert contribution == pytest.approx(expected)

    def test_exactly_7_days_out_is_zero(self):
        # November 23 — exactly 7 days from EOM, on the boundary that resolves
        # to zero.
        assert calendar_pressure_contribution(date(2026, 11, 23)) == 0.0

    def test_q1_end_gets_eoq_bonus(self):
        # March 30 — 1 day from EOM, Q1 end, no year-end.
        contribution = calendar_pressure_contribution(date(2026, 3, 30))
        # Peak EOM x Q-end bonus 1.4 -> caps at max.
        assert contribution == FACTOR_WEIGHTS["calendar_pressure"]


# ---- relative_delta_contribution ----


class TestRelativeDelta:
    def test_no_change_returns_zero(self):
        assert relative_delta_contribution("discount_depth", 10.0, 10.0) == 0.0

    def test_missing_current_returns_zero(self):
        assert relative_delta_contribution("discount_depth", None, 10.0) == 0.0

    def test_missing_baseline_returns_zero(self):
        assert relative_delta_contribution("discount_depth", 10.0, None) == 0.0

    def test_zero_baseline_returns_zero(self):
        # Division-by-zero guard — when baseline is 0 we can't compute a ratio.
        assert relative_delta_contribution("discount_depth", 10.0, 0.0) == 0.0

    def test_full_positive_change_caps_at_max(self):
        # 100% increase, capped at default max_pct_change=0.5 → full positive.
        c = relative_delta_contribution("discount_depth", current=20.0, baseline=10.0)
        assert c == FACTOR_WEIGHTS["discount_depth"]

    def test_full_negative_change_caps_at_negative_max(self):
        c = relative_delta_contribution("discount_depth", current=5.0, baseline=10.0)
        # 50% decrease → exactly -max_pct_change → -full contribution.
        assert c == -FACTOR_WEIGHTS["discount_depth"]

    def test_25pct_increase_gives_half_contribution(self):
        # 25% / 50% ratio = 0.5; contribution scales linearly.
        c = relative_delta_contribution("inventory_density", current=125.0, baseline=100.0)
        assert c == pytest.approx(FACTOR_WEIGHTS["inventory_density"] * 0.5)

    def test_unfavorable_direction_inverts_sign(self):
        # Effective price up = bad for buyer. Same magnitude, opposite sign.
        c = relative_delta_contribution(
            "effective_price_trend",
            current=30000.0,
            baseline=20000.0,
            direction="positive_is_unfavorable",
        )
        # 50% increase but unfavorable → maxes at -15 (the factor's weight).
        assert c == -FACTOR_WEIGHTS["effective_price_trend"]


# ---- composite_from_factors ----


class TestCompositeFromFactors:
    def test_empty_factors_gives_quiet_zero(self):
        result = composite_from_factors({})
        assert result.composite_index == 0.0
        assert result.variance_score == 0.0
        assert result.flower_position == 0.0
        assert result.label == "Quiet"

    def test_all_aligned_positive_caps_at_100(self):
        # Every factor at its max contribution → 30+25+20+15+10 = 100.
        contribs = dict(FACTOR_WEIGHTS)
        result = composite_from_factors(contribs)
        assert result.composite_index == 100.0
        assert result.flower_position == 1.0
        assert result.label == "Now"

    def test_all_aligned_negative_caps_at_negative_100(self):
        contribs = {k: -v for k, v in FACTOR_WEIGHTS.items()}
        result = composite_from_factors(contribs)
        assert result.composite_index == -100.0
        assert result.flower_position == -1.0
        assert result.label == "Not yet"

    def test_lean_now_at_threshold(self):
        # Sum to exactly LEAN_NOW_THRESHOLD → labeled "Now".
        result = composite_from_factors({"discount_depth": LEAN_NOW_THRESHOLD})
        assert result.composite_index == LEAN_NOW_THRESHOLD
        assert result.label == "Now"

    def test_quiet_just_below_threshold(self):
        result = composite_from_factors({"discount_depth": LEAN_NOW_THRESHOLD - 0.1})
        assert result.label == "Quiet"

    def test_lean_not_yet_at_threshold(self):
        result = composite_from_factors({"discount_depth": LEAN_NOT_YET_THRESHOLD})
        assert result.label == "Not yet"

    def test_variance_when_factors_disagree(self):
        # One factor at max+, one at max-, others zero — high disagreement.
        contribs = {"discount_depth": 20.0, "inventory_density": -20.0}
        result = composite_from_factors(contribs)
        # Composite averages out, but variance is non-trivial.
        assert result.variance_score > 0.5
        # Composite ~ 0 → Quiet label even though factors are loud.
        assert result.label == "Quiet"

    def test_variance_when_factors_agree(self):
        # Both factors aligned at +10 — low variance, high composite.
        contribs = {"discount_depth": 10.0, "inventory_density": 10.0}
        result = composite_from_factors(contribs)
        assert result.composite_index == 20.0
        assert result.variance_score == pytest.approx(0.0)

    def test_clipping_above_100(self):
        # Sum > 100 (shouldn't happen if weights respected but defensive check).
        # Using oversized contributions.
        contribs = {"discount_depth": 80.0, "inventory_density": 70.0}
        result = composite_from_factors(contribs)
        assert result.composite_index == 100.0
        assert result.flower_position == 1.0
