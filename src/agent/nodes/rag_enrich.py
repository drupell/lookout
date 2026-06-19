"""RAG enrichment — retrieves relevant incentive/tax credit context for scoring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.rag.retriever import retrieve

if TYPE_CHECKING:
    from src.agent.state import AgentState


def rag_enrich(state: AgentState) -> dict[str, Any]:
    """Retrieve relevant context from indexed documents for deal scoring."""
    started_at = datetime.now(UTC).isoformat()

    listings = state.get("validated_listings", [])

    # Build query from listing makes/models
    makes_models = set()
    for listing in listings:
        makes_models.add(f"{listing.get('make', '')} {listing.get('model', '')}")

    query = f"EV tax credit incentives eligibility {' '.join(makes_models)}"

    rag_chunks = retrieve(query, top_k=5)

    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "rag_enrich",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "rag_retriever", "query": query}],
        "output_record_count": len(rag_chunks),
        "guardrail_result": "PENDING",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    return {
        "rag_context": rag_chunks,
        "nodes_executed": existing_nodes,
    }
