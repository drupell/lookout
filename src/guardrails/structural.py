"""Pydantic v2 structural guardrails for every LangGraph node output.

Each model validates the shape and basic semantics of a node's output
before the graph allows the next node to execute.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# --- Listings ---


class ListingRecord(BaseModel):
    listing_id: str
    make: str
    model: str
    year: int = Field(ge=2020, le=2027)
    msrp: float = Field(gt=0)
    selling_price: float = Field(gt=0)
    effective_apr: float | None = None
    lease_monthly: float | None = None
    dealer_name: str
    dealer_distance_miles: float = Field(ge=0)
    url: str
    is_ev: bool = True

    @model_validator(mode="after")
    def price_must_be_below_msrp_or_flagged(self) -> ListingRecord:
        if self.selling_price > self.msrp * 1.15:
            raise ValueError(
                f"Selling price {self.selling_price} is >15% above MSRP {self.msrp} "
                "— likely a parse error"
            )
        return self


class ListingsOutput(BaseModel):
    listings: list[ListingRecord] = Field(min_length=0)
    source: str
    fetched_at: str


# --- Trade-In ---


class TradeInSource(BaseModel):
    source_name: str
    estimate_usd: float = Field(gt=0)
    estimate_type: str  # "trade_in" | "private_party" | "instant_offer"
    fetched_at: str


class TradeInOutput(BaseModel):
    """Trade-in valuation output. Zero/empty values indicate no sources succeeded —
    the graph proceeds with no trade-in value factored into scoring."""

    vin: str
    sources: list[TradeInSource] = Field(default_factory=list)
    average_estimate_usd: float = Field(ge=0)
    lowest_estimate_usd: float = Field(ge=0)
    highest_estimate_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def estimates_must_be_consistent(self) -> TradeInOutput:
        if not self.sources:
            # No-sources case: all estimates must be zero
            if (
                self.average_estimate_usd != 0
                or self.lowest_estimate_usd != 0
                or self.highest_estimate_usd != 0
            ):
                raise ValueError("With no sources, all estimates must be 0")
            return self
        if self.lowest_estimate_usd > self.highest_estimate_usd:
            raise ValueError(
                f"Lowest estimate ({self.lowest_estimate_usd}) > "
                f"highest estimate ({self.highest_estimate_usd})"
            )
        if not (self.lowest_estimate_usd <= self.average_estimate_usd <= self.highest_estimate_usd):
            raise ValueError("Average estimate must be between lowest and highest")
        return self


# --- RAG Enrichment ---


class RAGChunk(BaseModel):
    content: str = Field(min_length=1)
    source_document: str
    relevance_score: float = Field(ge=0.0, le=1.0)


class RAGEnrichmentOutput(BaseModel):
    chunks: list[RAGChunk]
    query_used: str


# --- Scored Deals ---


def _clamp_unit(v: object) -> object:
    """Clamp numeric inputs to [0.0, 1.0] before Field validation.

    LLM batch output is occasionally noisy on sub-scores (slightly negative or
    slightly > 1). Rejecting the whole batch over a rounding error wastes a
    run; clamping preserves the structural intent (a 0-1 score) while keeping
    the pipeline alive. Non-numeric input falls through untouched so type
    errors still surface clearly.
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return max(0.0, min(1.0, float(v)))
    return v


class ScoreBreakdown(BaseModel):
    price_score: float = Field(ge=0.0, le=1.0)
    financing_score: float = Field(ge=0.0, le=1.0)
    incentive_score: float = Field(ge=0.0, le=1.0)
    preference_match_score: float = Field(ge=0.0, le=1.0)
    reasoning: str

    @field_validator(
        "price_score",
        "financing_score",
        "incentive_score",
        "preference_match_score",
        mode="before",
    )
    @classmethod
    def _clamp(cls, v: object) -> object:
        return _clamp_unit(v)


class ScoredDeal(BaseModel):
    listing_id: str
    overall_score: float = Field(ge=0.0, le=1.0)
    score_breakdown: ScoreBreakdown
    effective_out_of_pocket_usd: float
    # 0 is legitimate when no trade-in sources are configured or all scrapers failed.
    trade_in_value_at_scoring: float = Field(ge=0)
    # Empty list is the natural state (no incentives apply) — don't fail a
    # whole run just because the LLM omitted the field on one row.
    applicable_incentives: list[str] = Field(default_factory=list)

    @field_validator("overall_score", mode="before")
    @classmethod
    def _clamp_overall(cls, v: object) -> object:
        return _clamp_unit(v)


class ScoredDealsOutput(BaseModel):
    scored_deals: list[ScoredDeal]
    model_used: str
    scored_at: str


# --- Email Drafts ---


class EmailDraft(BaseModel):
    listing_id: str
    dealer_name: str
    subject: str = Field(min_length=5, max_length=200)
    body: str = Field(min_length=50)
    deal_score: float = Field(ge=0.0, le=1.0)


class EmailDraftsOutput(BaseModel):
    drafts: list[EmailDraft] = Field(min_length=1)
    drafted_at: str


# --- Semantic Review ---


class SemanticReviewResult(BaseModel):
    approved: bool
    flags: list[str]
    reasoning: str

    @model_validator(mode="after")
    def flags_must_match_approval(self) -> SemanticReviewResult:
        if not self.approved and len(self.flags) == 0:
            raise ValueError("Rejected review must include at least one flag")
        return self


class SemanticReviewOutput(BaseModel):
    reviews: list[SemanticReviewResult]
    reviewed_at: str


# --- Persist Results ---


class PersistResultsOutput(BaseModel):
    deals_written: int = Field(ge=0)
    drafts_written: int = Field(ge=0)
    run_record_written: bool
    persisted_at: str


# --- Persist Market Snapshot ---


class MarketSnapshotPayload(BaseModel):
    """Validates a computed market snapshot before it's written to DynamoDB.

    The composite index lives in [-100, +100] by design (see
    src/agent/market_signal_math.py). Range constraints catch math bugs
    (e.g. a missing /100 that produces a composite of 8800.0) before they
    pollute the time series — and remember, snapshots have no TTL, so a
    bad row sits on the chart for 24 months.

    The label is restricted to the three editorial states the slider can
    surface; anything else means the headline math drifted from the math
    layer's thresholds, which would be a real regression.
    """

    composite_index: float = Field(ge=-100.0, le=100.0)
    factor_contributions: dict[str, float]
    variance_score: float = Field(ge=0.0, le=1.0)
    flower_position: float = Field(ge=-1.0, le=1.0)
    label: Literal["Now", "Quiet", "Not yet"]

    @field_validator("factor_contributions")
    @classmethod
    def _factor_contribs_within_bounds(cls, v: dict[str, float]) -> dict[str, float]:
        # Each factor's contribution is capped at ±20 by the math layer's
        # FACTOR_WEIGHTS; ±25 here gives a small safety margin for numerical
        # drift without letting a wildly broken factor poison the snapshot.
        for name, contrib in v.items():
            if not -25.0 <= contrib <= 25.0:
                raise ValueError(f"factor {name!r} out of bounds: {contrib}")
        return v
