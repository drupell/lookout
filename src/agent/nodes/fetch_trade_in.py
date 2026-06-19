"""Fetch trade-in value estimates for the owner's vehicle."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.state import AgentState

logger = logging.getLogger(__name__)


def fetch_trade_in(state: AgentState) -> dict[str, Any]:
    """Fetch trade-in estimates. In TEST_MODE, returns fixture data."""
    started_at = datetime.now(UTC).isoformat()

    if os.environ.get("TEST_MODE", "").lower() == "true":
        fixture_path = (
            Path(__file__).parent.parent.parent.parent
            / "evals"
            / "fixtures"
            / "trade_in_sample.json"
        )
        if fixture_path.exists():
            trade_in_data = json.loads(fixture_path.read_text())
        else:
            trade_in_data = {
                "vin": "SAMPLEVIN00000000",
                "sources": [
                    {
                        "source_name": "kbb",
                        "estimate_usd": 20000.0,
                        "estimate_type": "trade_in",
                        "fetched_at": datetime.now(UTC).isoformat(),
                    }
                ],
                "average_estimate_usd": 20000.0,
                "lowest_estimate_usd": 20000.0,
                "highest_estimate_usd": 20000.0,
            }
    else:
        from src.tools.trade_in.aggregator import get_trade_in_estimate

        try:
            trade_in_data = get_trade_in_estimate()
        except ValueError:
            logger.warning(
                "Trade-in scrapers returned no estimates — continuing with zero trade-in value"
            )
            trade_in_data = {
                "vin": "unknown",
                "sources": [],
                "average_estimate_usd": 0,
                "lowest_estimate_usd": 0,
                "highest_estimate_usd": 0,
            }

    completed_at = datetime.now(UTC).isoformat()

    node_record = {
        "node": "fetch_trade_in",
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_calls": [{"tool": "trade_in_aggregator", "test_mode": state.get("test_mode", False)}],
        "output_record_count": 1,
        "guardrail_result": "PENDING",
    }

    existing_nodes = list(state.get("nodes_executed", []))
    existing_nodes.append(node_record)

    return {
        "trade_in_estimate": trade_in_data,
        "nodes_executed": existing_nodes,
    }
