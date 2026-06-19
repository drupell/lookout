"""Structural eval: Deduplication logic filters duplicate listing IDs.

No LLM calls required — runs in <5s.
"""

from src.agent.nodes.filter_new_deals import filter_new_deals


class TestDeduplication:
    def test_duplicate_listing_ids_deduplicated(self):
        state = {
            "deals_above_threshold": [
                {"listing_id": "LST-001", "overall_score": 0.9},
                {"listing_id": "LST-001", "overall_score": 0.9},  # duplicate
                {"listing_id": "LST-002", "overall_score": 0.8},
            ],
            "nodes_executed": [],
        }

        result = filter_new_deals(state)
        new_deals = result["new_deals"]

        assert len(new_deals) == 2
        ids = [d["listing_id"] for d in new_deals]
        assert ids == ["LST-001", "LST-002"]

    def test_empty_input_returns_empty(self):
        state = {
            "deals_above_threshold": [],
            "nodes_executed": [],
        }

        result = filter_new_deals(state)
        assert len(result["new_deals"]) == 0

    def test_all_unique_passes_through(self):
        state = {
            "deals_above_threshold": [
                {"listing_id": "LST-001", "overall_score": 0.9},
                {"listing_id": "LST-002", "overall_score": 0.8},
                {"listing_id": "LST-003", "overall_score": 0.7},
            ],
            "nodes_executed": [],
        }

        result = filter_new_deals(state)
        assert len(result["new_deals"]) == 3
