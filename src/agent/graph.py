"""LangGraph StateGraph definition — the orchestration core.

Defines the full agent graph with guardrail validation between every node.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

if TYPE_CHECKING:
    from collections.abc import Callable

from src.agent.nodes.draft_emails import draft_emails
from src.agent.nodes.fetch_listings import fetch_listings
from src.agent.nodes.fetch_trade_in import fetch_trade_in
from src.agent.nodes.filter_new_deals import filter_new_deals
from src.agent.nodes.persist_market_snapshot import persist_market_snapshot
from src.agent.nodes.persist_results import persist_results
from src.agent.nodes.rag_enrich import rag_enrich
from src.agent.nodes.score_deals import score_deals
from src.agent.state import AgentState
from src.guardrails.registry import (
    validate_node_output,
    verify_registry_completeness,
)

# --- Guardrail wrapper nodes ---


def validate_listings(state: AgentState) -> dict[str, Any]:
    """Validate fetch_listings output via structural guardrail."""
    raw = state.get("raw_listings", [])
    now = datetime.now(UTC).isoformat()
    output_data = {"listings": raw, "source": "scraper", "fetched_at": now}

    result = validate_node_output("fetch_listings", output_data)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "fetch_listings",
                "errors": result.errors,
                "timestamp": now,
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"Listings guardrail failed: {result.errors}",
        }

    # Update the guardrail_result in the last node record
    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "fetch_listings":
            n["guardrail_result"] = "PASS"
            break

    return {
        "validated_listings": raw,
        "nodes_executed": nodes,
        "guardrail_triggers": existing_triggers,
    }


def validate_trade_in(state: AgentState) -> dict[str, Any]:
    """Validate fetch_trade_in output via structural guardrail."""
    trade_in = state.get("trade_in_estimate", {})
    result = validate_node_output("fetch_trade_in", trade_in)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "fetch_trade_in",
                "errors": result.errors,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"Trade-in guardrail failed: {result.errors}",
        }

    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "fetch_trade_in":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


def validate_rag(state: AgentState) -> dict[str, Any]:
    """Validate rag_enrich output."""
    chunks = state.get("rag_context", [])
    output_data = {"chunks": chunks, "query_used": "enrichment query"}
    result = validate_node_output("rag_enrich", output_data)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "rag_enrich",
                "errors": result.errors,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"RAG guardrail failed: {result.errors}",
        }

    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "rag_enrich":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


def validate_scores(state: AgentState) -> dict[str, Any]:
    """Validate score_deals output."""
    scored = state.get("scored_deals", [])
    now = datetime.now(UTC).isoformat()
    output_data = {"scored_deals": scored, "model_used": "configured", "scored_at": now}
    result = validate_node_output("score_deals", output_data)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "score_deals",
                "errors": result.errors,
                "timestamp": now,
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"Scores guardrail failed: {result.errors}",
        }

    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "score_deals":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


def validate_drafts(state: AgentState) -> dict[str, Any]:
    """Validate draft_emails output + run semantic review."""
    drafts = state.get("email_drafts", [])
    now = datetime.now(UTC).isoformat()
    output_data = {"drafts": drafts, "drafted_at": now}
    result = validate_node_output("draft_emails", output_data)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "draft_emails",
                "errors": result.errors,
                "timestamp": now,
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"Drafts guardrail failed: {result.errors}",
        }

    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "draft_emails":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


def validate_market_snapshot(state: AgentState) -> dict[str, Any]:
    """Validate the computed market snapshot via its structural guardrail.

    The node validates internally before writing (belt-and-suspenders), but
    the graph contract still requires a registered guardrail check at the
    wrapper layer. Absent `market_snapshot` in state means the node skipped
    the write for a benign reason (no user_id, prior read failure, etc.) —
    that's not a validation failure, just nothing to validate.
    """
    snapshot = state.get("market_snapshot")
    now = datetime.now(UTC).isoformat()

    nodes = list(state.get("nodes_executed", []))
    existing_triggers = list(state.get("guardrail_triggers", []))

    if not snapshot:
        # Node skipped the write — pass-through, mark the audit record.
        for n in reversed(nodes):
            if n["node"] == "persist_market_snapshot":
                n["guardrail_result"] = "SKIPPED"
                break
        return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}

    result = validate_node_output("persist_market_snapshot", snapshot)

    if not result.passed:
        existing_triggers.append(
            {
                "node": "persist_market_snapshot",
                "errors": result.errors,
                "timestamp": now,
            }
        )
        # A bad snapshot does NOT block the run — persist_results already
        # wrote the user-facing deals + run record on this terminal path.
        # We surface the failure in guardrail_triggers for observability.
        for n in reversed(nodes):
            if n["node"] == "persist_market_snapshot":
                n["guardrail_result"] = "FAIL"
                break
        return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}

    for n in reversed(nodes):
        if n["node"] == "persist_market_snapshot":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


def validate_persist(state: AgentState) -> dict[str, Any]:
    """Validate persist_results output via structural guardrail.

    Counts are derived from state: persist_results writes the full scored-deal
    set, and email_drafts is absent on the no-deals/skip-drafting paths. The
    run record is written by the handler on every terminal path.
    """
    now = datetime.now(UTC).isoformat()
    output_data = {
        "deals_written": len(state.get("scored_deals", [])),
        "drafts_written": len(state.get("email_drafts", [])),
        "run_record_written": True,
        "persisted_at": now,
    }
    result = validate_node_output("persist_results", output_data)

    existing_triggers = list(state.get("guardrail_triggers", []))
    if not result.passed:
        existing_triggers.append(
            {
                "node": "persist_results",
                "errors": result.errors,
                "timestamp": now,
            }
        )
        return {
            "guardrail_triggers": existing_triggers,
            "status": "GUARDRAIL_BLOCKED",
            "error": f"Persist guardrail failed: {result.errors}",
        }

    nodes = list(state.get("nodes_executed", []))
    for n in reversed(nodes):
        if n["node"] == "persist_results":
            n["guardrail_result"] = "PASS"
            break

    return {"nodes_executed": nodes, "guardrail_triggers": existing_triggers}


# --- Routing functions ---


def should_continue_after_guardrail(state: AgentState) -> str:
    """Route based on guardrail result — continue or end with error."""
    if state.get("status") == "GUARDRAIL_BLOCKED":
        return "end"
    return "continue"


def has_deals_above_threshold(state: AgentState) -> str:
    """Route based on whether any deals qualify for notification.

    Honors SKIP_DRAFTING=true env var for iterating on scoring without burning
    tokens on email drafting + semantic review. When skipping, deals still flow
    through persist_results so they show up in DynamoDB and the dashboard.
    """
    import os

    if os.environ.get("SKIP_DRAFTING", "").lower() == "true":
        return "skip_drafting"
    new_deals = state.get("new_deals", [])
    if new_deals:
        return "has_deals"
    return "no_deals"


# --- Graph construction ---


def build_graph() -> StateGraph:
    """Build and return the full agent StateGraph.

    Verifies guardrail registry completeness at build time.
    """
    # All node names that must have guardrails registered
    all_nodes = [
        "fetch_listings",
        "fetch_trade_in",
        "rag_enrich",
        "score_deals",
        "draft_emails",
        "semantic_review",
        "persist_results",
        "persist_market_snapshot",
        "filter_new_deals",
    ]
    verify_registry_completeness(all_nodes)

    graph = StateGraph(AgentState)

    # Add all nodes
    graph.add_node("fetch_listings", fetch_listings)
    graph.add_node("validate_listings", validate_listings)
    graph.add_node("fetch_trade_in", fetch_trade_in)
    graph.add_node("validate_trade_in", validate_trade_in)
    graph.add_node("rag_enrich", rag_enrich)
    graph.add_node("validate_rag", validate_rag)
    graph.add_node("score_deals", score_deals)
    graph.add_node("validate_scores", validate_scores)
    graph.add_node("filter_new_deals", filter_new_deals)
    graph.add_node("draft_emails", draft_emails)
    graph.add_node("validate_drafts", validate_drafts)
    graph.add_node("persist_results", persist_results)
    graph.add_node("validate_persist", validate_persist)
    graph.add_node("persist_market_snapshot", persist_market_snapshot)
    graph.add_node("validate_market_snapshot", validate_market_snapshot)

    # Set entry point
    graph.set_entry_point("fetch_listings")

    # Wire the graph
    graph.add_edge("fetch_listings", "validate_listings")
    graph.add_conditional_edges(
        "validate_listings",
        should_continue_after_guardrail,
        {
            "continue": "fetch_trade_in",
            "end": END,
        },
    )

    graph.add_edge("fetch_trade_in", "validate_trade_in")
    graph.add_conditional_edges(
        "validate_trade_in",
        should_continue_after_guardrail,
        {
            "continue": "rag_enrich",
            "end": END,
        },
    )

    graph.add_edge("rag_enrich", "validate_rag")
    graph.add_conditional_edges(
        "validate_rag",
        should_continue_after_guardrail,
        {
            "continue": "score_deals",
            "end": END,
        },
    )

    graph.add_edge("score_deals", "validate_scores")
    graph.add_conditional_edges(
        "validate_scores",
        should_continue_after_guardrail,
        {
            "continue": "filter_new_deals",
            "end": END,
        },
    )

    # Every terminal path persists the current scored-deal snapshot. The
    # no-deals path no longer skips persistence — `new_deals` only governs
    # email drafting, never what gets stored.
    graph.add_conditional_edges(
        "filter_new_deals",
        has_deals_above_threshold,
        {
            "has_deals": "draft_emails",
            "no_deals": "persist_results",
            "skip_drafting": "persist_results",
        },
    )

    graph.add_edge("draft_emails", "validate_drafts")
    graph.add_conditional_edges(
        "validate_drafts",
        should_continue_after_guardrail,
        {
            "continue": "persist_results",
            "end": END,
        },
    )

    graph.add_edge("persist_results", "validate_persist")
    # validate_persist always proceeds to the market-snapshot node. Even when
    # the persist_results guardrail blocks the main run, the snapshot still
    # captures what data we did have (marked partial) — that's the calibration
    # log promise: every run produces a row.
    graph.add_conditional_edges(
        "validate_persist",
        should_continue_after_guardrail,
        {
            "continue": "persist_market_snapshot",
            "end": "persist_market_snapshot",
        },
    )

    graph.add_edge("persist_market_snapshot", "validate_market_snapshot")
    graph.add_edge("validate_market_snapshot", END)

    return graph


def create_runnable():
    """Create a compiled, runnable graph instance."""
    graph = build_graph()
    return graph.compile()


def invoke_agent(
    test_mode: bool = False,
    environment: str = "dev",
    user_id: str | None = None,
    *,
    run_id: str | None = None,
    on_node_complete: Callable[[str, int], None] | None = None,
) -> AgentState:
    """Invoke the full agent graph and return final state.

    Args:
        test_mode: when True, skips real I/O (scrapers, LLM, DynamoDB writes).
        environment: tag stored on the run record.
        user_id: when given, downstream nodes load that user's preferences
            (bundled defaults + global + per-user overrides). None = legacy
            single-tenant mode.
        run_id: pre-generated run id. Required when the caller wants the
            running row written by the worker to share the run's id (so the
            in-flight indicator can find it). Falls back to a fresh uuid.
        on_node_complete: optional callback fired after each LangGraph node
            yields. Receives `(node_name, completed_count)`. Used by the
            worker to update the in-flight row's progress.
    """
    import os

    if test_mode:
        os.environ["TEST_MODE"] = "true"

    runnable = create_runnable()

    initial_state: AgentState = {
        "run_id": run_id or str(uuid.uuid4()),
        "environment": environment,
        "test_mode": test_mode,
        "nodes_executed": [],
        "guardrail_triggers": [],
        "token_usage": {"input": 0, "output": 0},
        "total_cost_usd": 0.0,
        "status": "IN_PROGRESS",
        "error": None,
    }
    if user_id:
        initial_state["user_id"] = user_id

    if on_node_complete is None:
        result = runnable.invoke(initial_state)
    else:
        # Stream mode: get an update per node so the in-flight row reflects
        # real progress. Each chunk is `{node_name: state_update}`. We merge
        # updates into a working state to reconstruct the final result.
        completed = 0
        result = dict(initial_state)
        for chunk in runnable.stream(initial_state):
            for node_name, state_update in chunk.items():
                completed += 1
                try:
                    on_node_complete(node_name, completed)
                except Exception:
                    # Progress reporting should never break the run.
                    import logging

                    logging.getLogger(__name__).exception(
                        "on_node_complete callback failed at node=%s", node_name
                    )
                if isinstance(state_update, dict):
                    result.update(state_update)

    # Set final status if not already set by a guardrail
    if result.get("status") == "IN_PROGRESS":
        result["status"] = "SUCCESS"

    return result
