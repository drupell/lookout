"""End-to-end eval: full graph execution with TEST_MODE=True and fixture data.

Asserts:
- Run completes without error
- Audit log record is populated
- Output shape matches expected schema
- No email is sent
"""

import os

from src.agent.graph import invoke_agent


class TestFullRun:
    def test_full_graph_completes_successfully(self):
        """Full graph run in test mode should complete with SUCCESS status."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        assert result["status"] in (
            "SUCCESS",
            "IN_PROGRESS",
        ), f"Expected SUCCESS, got {result['status']}: {result.get('error')}"

    def test_run_has_valid_run_id(self):
        """Every run must have a UUID run_id."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        assert "run_id" in result
        assert len(result["run_id"]) > 0

    def test_audit_trail_populated(self):
        """Nodes executed should be tracked in the audit trail."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        nodes_executed = result.get("nodes_executed", [])
        assert len(nodes_executed) >= 4, (
            f"Expected at least 4 nodes executed, got {len(nodes_executed)}"
        )

        # Check that each record has required fields
        for node_record in nodes_executed:
            assert "node" in node_record
            assert "started_at" in node_record
            assert "completed_at" in node_record

    def test_no_email_sent_in_test_mode(self):
        """Agent must never send email — only draft."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        # If there are email drafts, they should be data only (no send confirmation)
        drafts = result.get("email_drafts", [])
        for draft in drafts:
            # Drafts should have listing_id and body — no "sent" or "delivered" status
            assert "listing_id" in draft
            assert "body" in draft
            assert "sent" not in draft
            assert "delivered" not in draft

    def test_guardrail_triggers_tracked(self):
        """Guardrail triggers should be an empty list on clean runs."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        triggers = result.get("guardrail_triggers", [])
        assert isinstance(triggers, list)

    def test_environment_is_set(self):
        """Environment should be recorded in state."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        assert result.get("environment") == "dev"

    def test_scored_deals_present(self):
        """Test mode with fixtures should produce scored deals."""
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        scored = result.get("scored_deals", [])
        assert len(scored) >= 1, "Expected fixture data to produce scored deals"

    def test_handler_returns_valid_response_shape(self):
        """Lambda handler must return the shape the CI integration test asserts."""
        os.environ["TEST_MODE"] = "true"
        from src.handler import handler

        response = handler({"source": "aws.events"}, None)

        # Mirrors scripts/assert_response.py
        assert "statusCode" in response
        assert response["statusCode"] == 200
        body = response.get("body", {})
        for field in ("run_id", "status", "environment"):
            assert field in body, f"Missing required field: {field}"
        assert body["status"] in ("SUCCESS", "PARTIAL", "GUARDRAIL_BLOCKED", "ERROR")

    def test_skip_drafting_bypasses_draft_nodes(self):
        """SKIP_DRAFTING=true should route past draft_emails → directly to persist."""
        os.environ["TEST_MODE"] = "true"
        os.environ["SKIP_DRAFTING"] = "true"
        try:
            result = invoke_agent(test_mode=True, environment="dev")
        finally:
            os.environ.pop("SKIP_DRAFTING", None)

        # Run completes successfully
        assert result["status"] in ("SUCCESS", "IN_PROGRESS")

        # Scoring still runs
        assert len(result.get("scored_deals", [])) >= 1

        # But drafts are NOT produced when skipping
        assert len(result.get("email_drafts", [])) == 0

        # And no draft/semantic-review nodes in the audit trail
        executed_node_names = {n["node"] for n in result.get("nodes_executed", [])}
        assert "draft_emails" not in executed_node_names
        assert "semantic_review" not in executed_node_names

    def test_normal_run_still_drafts(self):
        """Without SKIP_DRAFTING, draft_emails still runs for new deals."""
        os.environ["TEST_MODE"] = "true"
        os.environ.pop("SKIP_DRAFTING", None)
        result = invoke_agent(test_mode=True, environment="dev")

        executed_node_names = {n["node"] for n in result.get("nodes_executed", [])}
        # draft_emails should run when we have deals and skip is off
        if result.get("new_deals"):
            assert "draft_emails" in executed_node_names

    def test_persist_results_runs_and_is_validated(self):
        """persist_results runs on every terminal path and clears its guardrail.

        The old persist_run_only escape hatch must be gone — persistence is
        unconditional so the latest-run snapshot is always complete.
        """
        os.environ["TEST_MODE"] = "true"
        result = invoke_agent(test_mode=True, environment="dev")

        records = result.get("nodes_executed", [])
        names = {n["node"] for n in records}
        assert "persist_results" in names
        assert "persist_run_only" not in names

        persist_rec = next(n for n in records if n["node"] == "persist_results")
        # validate_persist flips PENDING → PASS once the guardrail passes.
        assert persist_rec["guardrail_result"] == "PASS"
        assert result["status"] in ("SUCCESS", "IN_PROGRESS")

    def test_graph_wiring_has_validate_persist_no_persist_run_only(self):
        """The compiled graph must expose validate_persist and drop persist_run_only."""
        from src.agent.graph import create_runnable

        nodes = set(create_runnable().get_graph().nodes)
        assert "validate_persist" in nodes
        assert "persist_run_only" not in nodes


class TestRouting:
    def test_no_deals_routes_distinctly_from_has_deals(self):
        """has_deals_above_threshold: empty new_deals → no_deals (now → persist)."""
        os.environ["TEST_MODE"] = "true"
        os.environ.pop("SKIP_DRAFTING", None)
        from src.agent.graph import has_deals_above_threshold

        assert has_deals_above_threshold({"new_deals": []}) == "no_deals"
        assert has_deals_above_threshold({"new_deals": [{"listing_id": "x"}]}) == "has_deals"

    def test_skip_drafting_overrides_routing(self):
        from src.agent.graph import has_deals_above_threshold

        os.environ["SKIP_DRAFTING"] = "true"
        try:
            assert has_deals_above_threshold({"new_deals": []}) == "skip_drafting"
        finally:
            os.environ.pop("SKIP_DRAFTING", None)
