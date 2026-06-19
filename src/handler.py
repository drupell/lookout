"""Lambda handler — entry point wrapping the LangGraph agent graph.

Three invocation modes (auto-detected by event shape):

  1. SQS event (Records[].eventSource == "aws:sqs") — multi-tenant. Each
     record carries `{"user_id": "..."}` and produces one agent run scoped
     to that user. Triggered by the scheduler Lambda or the API.
  2. Direct payload with `user_id` key — used for ad-hoc test invocations
     against a specific user.
  3. Anything else — legacy single-tenant mode (no user_id; uses bundled
     prefs + global overrides only). Lets the existing EventBridge schedule
     keep working through migration.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point — dispatches by event shape, returns aggregate result."""
    logger.info("Event received: %s", json.dumps(event, default=str))

    user_ids = _extract_user_ids(event)
    if user_ids:
        # Multi-tenant: one run per user_id in the event. `_run_for_user`
        # already returns a summary dict (status, deals_found, …), so we pass
        # it through as-is — re-running `_summarize` on it would double-count
        # and crash on the count fields (TypeError: int has no len()).
        results = [_run_for_user(user_id, context) for user_id in user_ids]
        return {
            "statusCode": 200,
            "body": {
                "processed_users": len(results),
                "runs": results,
            },
        }

    # Legacy single-tenant fallback
    return _run_for_user(user_id=None, context=context, wrap_response=True)


def refresh_macro_series(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Cron entry point — refresh FRED + Manheim + AFDC caches.

    Fired nightly by EventBridge. Each provider is independent — a failure
    in one (e.g. AFDC rate-limiting us, FRED key rotation, Manheim URL
    drift) doesn't block the others. The clients themselves never raise
    per the cross-source resilience design; they return None on failure
    and we count it as a 0 in the result summary.

    Idempotent on the timestamp axis: FRED writes observations keyed by
    the FRED-reported date, Manheim writes the first-of-month timestamp,
    AFDC writes "now()" — so re-running within a day overwrites the same
    rows rather than appending.
    """
    logger.info("refresh_macro_series: starting")

    # Lazy imports — these modules pull in httpx + the LLM provider and
    # we don't want to load them on the hot per-run path.
    from src.tools.external import afdc, fred, manheim_pulse

    # Each provider call is wrapped so a bug in one (unexpected exception
    # escaping the client's documented never-raise contract) doesn't take
    # the others down with it. The expected steady state is that the
    # clients themselves return None / 0 on transient failures; this is
    # defense in depth for bugs.
    def _safe(label: str, fn):
        try:
            return fn()
        except Exception:
            logger.exception("refresh_macro_series: %s raised; recording as failure", label)
            return None

    fred_result = _safe("fred", fred.refresh_all) or {}
    manheim_result = _safe("manheim", manheim_pulse.fetch_and_cache_index)
    # Baseline "warm cache" states — high EV-adoption states + the maintainer's
    # state(s). The /me/incentives handler also lazy-fetches a user's state on
    # first request when it isn't in this list, so adding a state here is purely
    # an optimization for first-load latency, not a correctness requirement.
    afdc_states = ["CA", "MA", "NY", "NJ", "CT", "OR", "WA", "MD", "IL", "CO", "DE"]
    afdc_result = _safe("afdc", lambda: afdc.refresh_for_states(afdc_states)) or {}

    summary = {
        "fred": fred_result,
        "manheim": 1 if manheim_result else 0,
        "afdc": afdc_result,
    }
    logger.info("refresh_macro_series: done %s", json.dumps(summary, default=str))
    return {"statusCode": 200, "body": summary}


# --- Internals ---


def _extract_user_ids(event: dict[str, Any]) -> list[str]:
    """Return user_ids from the event, supporting SQS batches and direct payloads."""
    # SQS batch
    records = event.get("Records") or []
    if records and any(r.get("eventSource") == "aws:sqs" for r in records):
        ids: list[str] = []
        for r in records:
            try:
                body = json.loads(r.get("body", "{}"))
                uid = body.get("user_id")
                if uid:
                    ids.append(uid)
                else:
                    logger.warning("SQS record missing user_id: %s", body)
            except json.JSONDecodeError:
                logger.exception("SQS record body not valid JSON")
        return ids

    # Direct payload (manual test invoke)
    if isinstance(event, dict) and event.get("user_id"):
        return [event["user_id"]]

    return []


def _run_for_user(
    user_id: str | None,
    context: Any,
    *,
    wrap_response: bool = False,
) -> dict[str, Any]:
    """Run the agent for a single user (or legacy single-tenant if user_id is None)."""
    import uuid
    from datetime import UTC, datetime

    environment = os.environ.get("ENVIRONMENT", "dev")
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    refresh_deals = os.environ.get("REFRESH_DEALS_ON_RUN", "false").lower() == "true"

    # Wipe stale deals before this run when refresh-on-run is on. Scoped per
    # user (via the GSI) so we never clobber other users' rows. Legacy
    # single-tenant runs use the full-table wipe for backward compatibility.
    if refresh_deals and not test_mode:
        try:
            if user_id:
                from src.memory.deal_store import wipe_user_deals

                wipe_user_deals(user_id)
            else:
                from src.memory.deal_store import wipe_all_deals

                wipe_all_deals()
        except Exception:
            logger.exception("Failed to wipe deals before run — continuing with stale data")

    # Generate run_id + started_at upfront so the in-flight row, the per-node
    # progress updates, and the final audit record all key on the same
    # `(run_id, started_at)` pair — avoids creating sibling rows.
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC).isoformat()

    from src.memory import run_state

    run_state.mark_running(run_id, user_id, started_at, environment)

    from src.agent.graph import invoke_agent

    def _on_node_complete(node_name: str, completed: int) -> None:
        run_state.update_progress(run_id, started_at, node_name, completed)

    try:
        result = invoke_agent(
            test_mode=test_mode,
            environment=environment,
            user_id=user_id,
            run_id=run_id,
            on_node_complete=_on_node_complete,
        )
    except Exception:
        logger.exception("Agent graph failed (user_id=%s)", user_id)
        from src.memory.run_store import write_run_record

        error_state: dict[str, Any] = {
            "run_id": run_id,
            "started_at": started_at,
            "environment": environment,
            "status": "ERROR",
            "scored_deals": [],
            "deals_above_threshold": [],
            "email_drafts": [],
            "nodes_executed": [],
            "guardrail_triggers": [],
            "token_usage": {},
            "total_cost_usd": 0,
        }
        if user_id:
            error_state["user_id"] = user_id
        # Same key as the RUNNING row → overwrites it, clearing in-flight.
        write_run_record(error_state, timestamp=started_at)
        raise

    # Persist the run record (carries user_id if present)
    from src.memory.run_store import write_run_record

    if user_id and "user_id" not in result:
        result["user_id"] = user_id
    result["started_at"] = started_at
    write_run_record(result, timestamp=started_at)

    # Persist trade-in data
    trade_in = result.get("trade_in_estimate")
    if trade_in:
        from src.memory.trade_in_store import write_trade_in_estimate

        write_trade_in_estimate(trade_in, result["run_id"])

    # Deals are persisted by the persist_results node on every terminal path
    # (the full scored-deal snapshot, tagged with run_id) — the handler no
    # longer writes them, avoiding a redundant second write.

    # Stamp the user row with last_run_at so the dashboard can detect
    # "settings changed since the last run" and warn that current deals were
    # scored against older preferences. Fail silently — the run itself is
    # already persisted; this is just a UX hint.
    # Only stamp the user's "latest run" pointers when the run actually
    # succeeded — a guardrail-blocked or errored run shouldn't wipe their last
    # good snapshot from the dashboard. The run audit record is still written
    # above regardless, so observability is preserved.
    if user_id and result.get("status") == "SUCCESS":
        try:
            _stamp_last_run_at(user_id, run_id)
        except Exception:
            logger.exception("Failed to stamp last_run_at for user_id=%s", user_id)

    summary = _summarize(result, environment=environment)
    logger.info("Agent run complete (user_id=%s): %s", user_id, json.dumps(summary, default=str))

    if wrap_response:
        return {"statusCode": 200, "body": summary}
    return summary


def _summarize(result: dict[str, Any], *, environment: str | None = None) -> dict[str, Any]:
    """Return the summary fields the API + scheduler care about."""
    summary: dict[str, Any] = {
        "run_id": result.get("run_id"),
        "user_id": result.get("user_id"),
        "status": result.get("status"),
        "environment": environment or os.environ.get("ENVIRONMENT", "dev"),
        "deals_found": len(result.get("scored_deals", [])),
        "deals_above_threshold": len(result.get("deals_above_threshold", [])),
        "drafts_produced": len(result.get("email_drafts", [])),
        "guardrail_triggers": len(result.get("guardrail_triggers", [])),
    }
    # Surface upstream-data failures (e.g. MarketCheck rate-limited) so the
    # dashboard can distinguish "no deals matched" from "we got nothing back
    # to score". Format: ["marketcheck:rate_limited", ...]
    source_errors = result.get("source_errors") or []
    if source_errors:
        summary["source_errors"] = source_errors
    return summary


def _stamp_last_run_at(user_id: str, run_id: str) -> None:
    """Write `last_run_at` and `last_run_id` on the user's row.

    `last_run_at` powers the dashboard's stale-settings banner (compare
    `updated_at` vs `last_run_at`). `last_run_id` is the pointer the deals
    API uses to scope the Deals tab to the latest completed run's snapshot.
    Only runs on the success path, so it always references a run that
    persisted its deals.
    """
    from datetime import UTC, datetime

    table_name = os.environ.get("USERS_TABLE_NAME")
    if not table_name:
        return
    import boto3

    table = boto3.resource("dynamodb").Table(table_name)
    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET last_run_at = :t, last_run_id = :r",
        ExpressionAttributeValues={
            ":t": datetime.now(UTC).isoformat(),
            ":r": run_id,
        },
    )
