"""AgentState — single source of truth for all data flowing through the LangGraph graph."""

from __future__ import annotations

from typing import Any, TypedDict


class NodeExecutionRecord(TypedDict):
    """Structured record of a single node's execution for the audit trail."""

    node: str
    started_at: str
    completed_at: str
    tool_calls: list[dict[str, Any]]
    output_record_count: int
    guardrail_result: str


class AgentState(TypedDict, total=False):
    """Accumulated state passed through every node in the LangGraph graph.

    Every field is optional (total=False) because nodes return partial updates.
    The graph runner merges these updates into the full state.
    """

    # Run metadata
    run_id: str
    environment: str
    test_mode: bool
    # Owning user (multi-tenant). None/missing = legacy single-tenant run.
    user_id: str

    # Listings fetched from scrapers
    raw_listings: list[dict[str, Any]]
    validated_listings: list[dict[str, Any]]

    # Trade-in data
    trade_in_estimate: dict[str, Any]

    # RAG context
    rag_context: list[dict[str, str]]

    # Scored deals
    scored_deals: list[dict[str, Any]]
    deals_above_threshold: list[dict[str, Any]]

    # New deals after deduplication
    new_deals: list[dict[str, Any]]

    # Email drafts
    email_drafts: list[dict[str, Any]]
    semantic_review_results: list[dict[str, Any]]

    # Handler-injected run metadata (set in src/handler.py)
    started_at: str

    # Computed market-signal snapshot — written by persist_market_snapshot
    # so validate_market_snapshot can re-run the guardrail at the graph layer.
    # Empty/absent means the node skipped the write (no user_id, prior-read
    # failure, etc.) — the validator treats absence as a no-op pass.
    market_snapshot: dict[str, Any]

    # Audit trail
    nodes_executed: list[NodeExecutionRecord]
    guardrail_triggers: list[dict[str, Any]]
    token_usage: dict[str, int]
    total_cost_usd: float
    status: str
    error: str | None
