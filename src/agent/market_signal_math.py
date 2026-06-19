"""Pure-function math for the market-signal feature.

These functions take primitive inputs (numbers, dates, dicts) and return
primitive outputs (numbers, dataclasses). No I/O, no DynamoDB, no LLM calls —
all of that lives at the boundary, so this module is exhaustively testable.

The composite buy/wait index is in `[-100, +100]`. Five factors each contribute
at most ±20 points (the weights below describe their max contribution; the
sign comes from the relative-vs-baseline computation). The composite is the
sum of the five contributions, clipped to the index range.

The flower position is the composite mapped to `[-1.0, +1.0]` — bloom (+1)
on the "Now" end of the slider, withered (-1) on "Not yet". The variance
score is in `[0.0, 1.0]` and drives how wide the halo around the flower is:
0 means all factors agree, 1 means they're pulling in opposite directions.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
from dataclasses import dataclass
from datetime import date  # noqa: TC003 - runtime usage in calendar_pressure_contribution()
from math import sqrt

# ---- Factor weights (max contribution magnitude, in index points) ----

# Six factors compose the index; their weights sum to 115. The composite math
# clips at ±100 so the index range stays honest — a small "saturation buffer"
# above 100 means an unusually-aligned set of factors registers as a clean
# "Strong lean" without one factor dominating the math. Each factor's
# contribution is in `[-weight, +weight]`.
FACTOR_WEIGHTS: dict[str, float] = {
    "discount_depth": 30.0,
    "inventory_density": 25.0,
    "calendar_pressure": 20.0,
    "effective_price_trend": 15.0,
    "auto_loan_apr_trend": 15.0,  # NEW — from FRED's TERMCBAUTO48NS
    "incentive_prevalence": 10.0,
}

# Which factors belong to the Macro vs Personal layer of the index. Surfaced
# on the snapshot row as `macro_index` / `personal_index` so the API can show
# the breakdown (and so the trend chart can render two lines when it lands).
MACRO_FACTORS: frozenset[str] = frozenset({"calendar_pressure", "auto_loan_apr_trend"})
PERSONAL_FACTORS: frozenset[str] = frozenset(
    {"discount_depth", "inventory_density", "effective_price_trend", "incentive_prevalence"}
)

# Headline state thresholds — used by the UI to pick between "Now" / "Quiet"
# / "Not yet" framing on the marker text.
LEAN_NOW_THRESHOLD = 15.0
LEAN_NOT_YET_THRESHOLD = -15.0


# ---- Calendar pressure ----

# Multipliers for known dealer-cycle moments. Calendar pressure is fully
# deterministic — same anywhere in the US, same for everyone, no data needed.
# This is the only factor that produces a non-zero signal on day 1 for a new
# user with no history, so it has to be honest about what it actually models.

# Last 3 days of month: dealer EOM push (commissions, regional quotas). Built
# as a smooth ramp from day -7 to day 0 (last day of month) so the slider
# doesn't snap discretely at the start of the EOM week.
_EOM_PEAK_DAYS = 3
_EOM_RAMP_DAYS = 7

# End-of-quarter is meaningfully larger than EOM-only — bonus tier of dealer
# incentives stacks. Multiplier applied IN ADDITION TO the EOM ramp for
# March / June / September / December.
_EOQ_BONUS_MULTIPLIER = 1.4

# December gets a year-end bonus on top of EOM+EOQ. Holiday promotions and
# manufacturer-incentive seasonality push this meaningfully higher.
_YEAR_END_BONUS_MULTIPLIER = 1.25


def calendar_pressure_contribution(today: date) -> float:
    """Return the calendar-pressure factor's contribution, in index points.

    Positive values lean toward "Now" (favorable buying timing); zero means
    no calendar pressure is in effect. Capped at the factor's max weight.

    Honest about what we model:
      - Smooth ramp toward end-of-month over the last 7 days (peak: 3 days).
      - End-of-quarter bonus (Mar/Jun/Sep/Dec) — dealers chase quarterly tiers.
      - Year-end bonus (Dec) — annual manufacturer incentive seasonality.

    We *don't* model: specific holidays (Memorial Day, July 4, Black Friday).
    Those vary by region and OEM enough that hardcoding them would mis-fire
    more than it'd help. They get folded into discount-depth + inventory
    factors via observed data instead.
    """
    max_contribution = FACTOR_WEIGHTS["calendar_pressure"]
    days_in_month = _stdlib_calendar.monthrange(today.year, today.month)[1]
    days_to_end = days_in_month - today.day

    # EOM ramp: 0 at >7 days from EOM, linear up to max as we hit day 0.
    if days_to_end >= _EOM_RAMP_DAYS:
        eom_factor = 0.0
    elif days_to_end <= _EOM_PEAK_DAYS:
        eom_factor = 1.0
    else:
        # Ramp from 0 at day -7 to 1.0 at day -3.
        span = _EOM_RAMP_DAYS - _EOM_PEAK_DAYS
        eom_factor = (_EOM_RAMP_DAYS - days_to_end) / span

    # Quarterly + year-end multipliers stack ON TOP of the EOM ramp.
    quarter_end_months = {3, 6, 9, 12}
    multiplier = 1.0
    if today.month in quarter_end_months:
        multiplier *= _EOQ_BONUS_MULTIPLIER
    if today.month == 12:
        multiplier *= _YEAR_END_BONUS_MULTIPLIER

    contribution = max_contribution * eom_factor * multiplier
    return min(contribution, max_contribution)


# ---- Per-factor delta math ----


def relative_delta_contribution(
    factor: str,
    current: float | None,
    baseline: float | None,
    *,
    direction: str = "positive_is_favorable",
    max_pct_change: float = 0.5,
) -> float:
    """Return a single data-driven factor's contribution, in index points.

    Computes a *relative delta* against a trailing baseline, capped at the
    factor's max weight. Drift-free by construction — inflation cancels out
    because both sides of the ratio drift together.

    Args:
        factor: the factor name, used to look up max contribution magnitude.
        current: today's observed value (e.g. median discount %-off-MSRP).
        baseline: the trailing-window median of the same metric.
        direction: "positive_is_favorable" means current > baseline pushes
            toward "Now" (e.g. discount depth — bigger discounts are better).
            "positive_is_unfavorable" means current > baseline pushes toward
            "Not yet" (e.g. effective price trend — higher is worse).
        max_pct_change: the change ratio that maps to full contribution
            magnitude. 0.5 means a 50% change vs baseline fully saturates the
            factor; larger changes still clip to the max. A reasonable default
            for monthly market metrics.

    Returns 0.0 when either value is missing (factor degrades to neutral —
    we don't fabricate a contribution from incomplete data).
    """
    if current is None or baseline is None or baseline == 0:
        return 0.0
    max_contribution = FACTOR_WEIGHTS[factor]
    pct_change = (current - baseline) / baseline
    # Clip then scale so the sign comes through naturally.
    clipped = max(-max_pct_change, min(max_pct_change, pct_change))
    scaled = (clipped / max_pct_change) * max_contribution
    if direction == "positive_is_unfavorable":
        scaled = -scaled
    return scaled


# ---- Composite + headline ----


@dataclass(frozen=True)
class CompositeResult:
    """A computed market-signal snapshot for one (user_or_market, timestamp)."""

    composite_index: float  # [-100, +100]
    factor_contributions: dict[str, float]
    variance_score: float  # [0.0, 1.0]
    flower_position: float  # [-1.0, +1.0]
    label: str  # "Now" | "Quiet" | "Not yet"


def composite_from_factors(factor_contributions: dict[str, float]) -> CompositeResult:
    """Compose the slider snapshot from already-computed factor contributions.

    Caller is responsible for computing each factor's contribution (using the
    helpers above) and passing a dict keyed by factor name. Missing factors
    are treated as zero contributions — they don't push the index either way.
    """
    # Sum the contributions; clip the composite to the index range.
    raw_total = sum(factor_contributions.values())
    composite = max(-100.0, min(100.0, raw_total))

    # Variance: are the factors saying the same thing, or fighting each other?
    # We compute it on the *signed* contributions so disagreement is real
    # disagreement (factor A says +12, factor B says -10 → high variance even
    # though average is small). Normalized to [0, 1] so the UI can map directly
    # to halo width.
    variance = _signed_variance(list(factor_contributions.values()))

    # Flower position maps composite into [-1, +1]; the marker on the slider.
    flower = composite / 100.0

    if composite >= LEAN_NOW_THRESHOLD:
        label = "Now"
    elif composite <= LEAN_NOT_YET_THRESHOLD:
        label = "Not yet"
    else:
        label = "Quiet"

    return CompositeResult(
        composite_index=composite,
        factor_contributions=dict(factor_contributions),
        variance_score=variance,
        flower_position=flower,
        label=label,
    )


def _signed_variance(contributions: list[float]) -> float:
    """Return a [0, 1] variance score reflecting factor disagreement.

    The natural definition: standard deviation of contributions / max possible
    standard deviation given the bounds. Empty/single-element inputs are zero
    (no disagreement possible). When all contributions agree in sign and
    magnitude, variance is 0; when they're maximally split between +20 and
    -20, variance approaches 1.
    """
    if len(contributions) < 2:
        return 0.0
    mean = sum(contributions) / len(contributions)
    sq_diffs = [(c - mean) ** 2 for c in contributions]
    sample_var = sum(sq_diffs) / len(contributions)
    std = sqrt(sample_var)
    # Maximum std for our factors: contributions bounded by ±20. The maximally
    # split case (half at +20, half at -20) has std = 20. So divide by 20 to
    # normalize, clamp to [0, 1] for safety.
    normalized = std / 20.0
    return min(1.0, max(0.0, normalized))
