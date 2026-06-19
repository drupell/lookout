"""Scheduler Lambda — fans out scheduled runs across all due users.

Triggered by EventBridge cron (hourly). For each user:
  1. Load their effective ScheduleConfig (bundled defaults + global + user overrides).
  2. Compute next_run_at if missing (the next configured weekday at time_of_day_utc).
  3. If next_run_at <= now, queue an SQS message and advance next_run_at to the
     following configured run.

Why scheduler advances next_run_at (not the worker):
  - Idempotency: if SQS send fails, next_run_at is NOT advanced — user retries
    next hour. If the worker fails after we queued, the user just runs again
    next cycle (the agent is idempotent at this scale).
  - Decoupling: the worker doesn't need to know its caller's scheduling state.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Enumerate users, queue any that are due. Returns counts for observability."""
    logger.info("Scheduler invoked: %s", json.dumps(event, default=str))

    queue_url = os.environ["RUNS_QUEUE_URL"]
    users_table = os.environ["USERS_TABLE_NAME"]
    now = datetime.now(UTC)

    import boto3

    sqs = boto3.client("sqs")
    table = boto3.resource("dynamodb").Table(users_table)

    user_ids = _list_user_ids(table)
    counts = {"users_seen": len(user_ids), "due": 0, "queued": 0, "failed": 0, "skipped": 0}

    from src.config.loader import load_preferences
    from src.scheduling import next_run_after

    for user_id in user_ids:
        try:
            schedule = load_preferences(user_id=user_id).schedule
        except Exception:
            logger.exception("Failed to load schedule for %s — skipping this cycle", user_id)
            counts["skipped"] += 1
            continue

        next_run_at = _read_next_run_at(table, user_id) or next_run_after(schedule, now)
        if next_run_at > now:
            continue
        counts["due"] += 1

        try:
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps({"user_id": user_id, "trigger": "schedule"}),
            )
            counts["queued"] += 1
        except Exception:
            logger.exception("Failed to queue run for %s — leaving next_run_at unchanged", user_id)
            counts["failed"] += 1
            continue

        # Advance to the following configured run. Use whichever of the just-
        # fired slot or "now" is later: advancing from the slot preserves
        # cadence on a slightly-late scheduler tick, while clamping to "now"
        # stops us from looping forever when next_run_at is wildly stale (a
        # past value left by validation/debugging, etc. — advancing from it by
        # whole weeks would just yield another past time).
        new_next = next_run_after(schedule, max(next_run_at, now))
        _write_next_run_at(table, user_id, new_next, last_run_at=now)

    logger.info("Scheduler done: %s", counts)
    return counts


# --- helpers ---


def _list_user_ids(table) -> list[str]:
    """Scan the Users table for user_ids. Cheap at our scale."""
    user_ids: list[str] = []
    response = table.scan(ProjectionExpression="user_id")
    user_ids.extend(item["user_id"] for item in response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(
            ProjectionExpression="user_id",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        user_ids.extend(item["user_id"] for item in response.get("Items", []))
    return user_ids


def _read_next_run_at(table, user_id: str) -> datetime | None:
    """Read user's next_run_at as a UTC datetime, or None if unset."""
    resp = table.get_item(Key={"user_id": user_id}, ProjectionExpression="next_run_at")
    raw = (resp.get("Item") or {}).get("next_run_at")
    if not raw:
        return None
    return _parse_iso(str(raw))


def _write_next_run_at(table, user_id: str, next_run_at: datetime, last_run_at: datetime) -> None:
    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET next_run_at = :n, last_run_at = :l",
        ExpressionAttributeValues={
            ":n": next_run_at.isoformat(),
            ":l": last_run_at.isoformat(),
        },
    )


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 timestamp; assume UTC if tz-naive."""
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
