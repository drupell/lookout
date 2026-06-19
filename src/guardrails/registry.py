"""Guardrail registry — maps every node to its required validation checks.

If a node is not registered here, the graph must fail at startup.
This ensures guardrails are never accidentally skipped.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from src.guardrails.structural import (
    EmailDraftsOutput,
    ListingsOutput,
    MarketSnapshotPayload,
    PersistResultsOutput,
    RAGEnrichmentOutput,
    ScoredDealsOutput,
    SemanticReviewOutput,
    TradeInOutput,
)


class GuardrailResult:
    """Result of a guardrail check — pass or fail with details."""

    def __init__(self, passed: bool, node: str, errors: list[str] | None = None):
        self.passed = passed
        self.node = node
        self.errors = errors or []

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"GuardrailResult({self.node}: {status})"


# Registry: node name → Pydantic model class used to validate its output
GUARDRAIL_REGISTRY: dict[str, type[BaseModel]] = {
    "fetch_listings": ListingsOutput,
    "fetch_trade_in": TradeInOutput,
    "rag_enrich": RAGEnrichmentOutput,
    "score_deals": ScoredDealsOutput,
    "draft_emails": EmailDraftsOutput,
    "semantic_review": SemanticReviewOutput,
    "persist_results": PersistResultsOutput,
    "persist_market_snapshot": MarketSnapshotPayload,
}

# Nodes that do not produce validated output (pure logic / routing)
EXEMPT_NODES: set[str] = {"filter_new_deals"}


def validate_node_output(node_name: str, output_data: dict[str, Any]) -> GuardrailResult:
    """Validate a node's output against its registered Pydantic model.

    Raises RuntimeError if the node has no registered guardrail and is not exempt.
    """
    if node_name in EXEMPT_NODES:
        return GuardrailResult(passed=True, node=node_name)

    model_class = GUARDRAIL_REGISTRY.get(node_name)
    if model_class is None:
        raise RuntimeError(
            f"Node '{node_name}' has no registered guardrail and is not exempt. "
            "Register it in GUARDRAIL_REGISTRY or add to EXEMPT_NODES."
        )

    try:
        model_class(**output_data)
        return GuardrailResult(passed=True, node=node_name)
    except ValidationError as e:
        error_messages = [f"{err['loc']}: {err['msg']}" for err in e.errors()]
        return GuardrailResult(passed=False, node=node_name, errors=error_messages)


def verify_registry_completeness(graph_nodes: list[str]) -> None:
    """Verify every node in the graph has a registered guardrail or is exempt.

    Call this at graph startup — fail loudly, not silently at runtime.
    """
    missing = []
    for node in graph_nodes:
        if node not in GUARDRAIL_REGISTRY and node not in EXEMPT_NODES:
            missing.append(node)

    if missing:
        raise RuntimeError(
            f"Nodes without registered guardrails: {missing}. "
            "Register them in GUARDRAIL_REGISTRY or add to EXEMPT_NODES."
        )
