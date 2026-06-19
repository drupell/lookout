"""Tests for the scheduler Lambda + SQS event-handling in the worker.

Covers:
  - Scheduler scans Users and queues one SQS message per user.
  - Worker handler routes SQS events to per-user runs.
  - API trigger_run drops the right message onto the queue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator

QUEUE_NAME = "lookout-test-runs"
USERS_TABLE_NAME = "lookout-test-users"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Activate moto + create the queue/table + set env vars. Yields URLs."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("USERS_TABLE_NAME", USERS_TABLE_NAME)
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        monkeypatch.setenv("RUNS_QUEUE_URL", queue_url)

        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName=USERS_TABLE_NAME,
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield {"queue_url": queue_url}


def _put_user(user_id: str, **fields: Any) -> None:
    table = boto3.resource("dynamodb").Table(USERS_TABLE_NAME)
    item = {"user_id": user_id, "email": f"{user_id}@example.com", "tier": "default", **fields}
    table.put_item(Item=item)


def _drain_queue(queue_url: str) -> list[dict[str, Any]]:
    sqs = boto3.client("sqs")
    messages: list[dict[str, Any]] = []
    while True:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
        msgs = resp.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            messages.append(json.loads(m["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return messages


# -------------------- Scheduler --------------------


class TestScheduler:
    def test_no_users_no_messages(self, aws: dict[str, str]) -> None:
        from src.scheduler.handler import handler

        result = handler({}, None)
        assert result["users_seen"] == 0
        assert result["queued"] == 0
        assert _drain_queue(aws["queue_url"]) == []

    def test_user_with_past_next_run_is_queued(self, aws: dict[str, str]) -> None:
        from src.scheduler.handler import handler

        _put_user("u-due", next_run_at="2020-01-01T00:00:00+00:00")

        result = handler({}, None)
        assert result["queued"] == 1
        msgs = _drain_queue(aws["queue_url"])
        assert msgs == [{"user_id": "u-due", "trigger": "schedule"}]

    def test_user_with_future_next_run_is_skipped(self, aws: dict[str, str]) -> None:
        from src.scheduler.handler import handler

        _put_user("u-not-due", next_run_at="2099-01-01T00:00:00+00:00")

        result = handler({}, None)
        assert result["queued"] == 0
        assert _drain_queue(aws["queue_url"]) == []

    def test_user_without_next_run_at_starts_at_first_aligned_slot(
        self, aws: dict[str, str]
    ) -> None:
        """Brand-new users have no next_run_at; scheduler picks one and (probably)
        skips them this tick — except when the aligned slot already passed today,
        which we simulate via tomorrow.
        """
        from src.scheduler.handler import handler

        _put_user("u-fresh")  # no next_run_at

        result = handler({}, None)
        # First tick: scheduler computes next_run_at for the future, doesn't queue
        assert result["queued"] == 0

    def test_advances_next_run_at_after_queuing(self, aws: dict[str, str]) -> None:
        from src.scheduler.handler import handler

        _put_user("u-cycle", next_run_at="2020-01-01T00:00:00+00:00")
        handler({}, None)

        # Read back the user — next_run_at advances to the next configured
        # run AND is clamped to the future (so a wildly-stale 2020 next_run_at
        # can't yield 2020-01-02 and loop the user every hourly tick).
        from datetime import UTC, datetime

        item = (
            boto3.resource("dynamodb")
            .Table(USERS_TABLE_NAME)
            .get_item(Key={"user_id": "u-cycle"})
            .get("Item")
        )
        next_run = datetime.fromisoformat(item["next_run_at"])
        assert next_run.weekday() == 3, "default schedule is Thursday"
        assert next_run.hour == 13, "default time is 13:00 UTC"
        assert next_run > datetime.now(UTC), "advance must land in the future"
        assert "last_run_at" in item


# (Next-run math is unit-tested in test_scheduling.py.)


# -------------------- Schedule via prefs (tier enforcement) --------------------


class TestScheduleTierGating:
    def test_byok_can_edit_days(self, aws: dict[str, str]) -> None:
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("byok-user", email="x@y.com", tier="byok")
        result = put_prefs("byok-user", {"schedule": {"days_of_week": [0, 3]}})
        assert result["effective"]["schedule"]["days_of_week"] == [0, 3]

    def test_default_cannot_edit_days(self, aws: dict[str, str]) -> None:
        from src.api._errors import HttpError
        from src.api._user_repo import upsert_user
        from src.api.prefs import put_prefs

        upsert_user("default-user", email="x@y.com", tier="default")
        with pytest.raises(HttpError) as exc:
            put_prefs("default-user", {"schedule": {"days_of_week": [0, 3]}})
        assert exc.value.status == 403
        assert "schedule.days_of_week" in exc.value.details["rejected_paths"]


# -------------------- API trigger --------------------


class TestApiTriggerRun:
    def test_sends_message_to_queue(self, aws: dict[str, str]) -> None:
        from src.api.runs import trigger_run

        result = trigger_run("alice")
        assert result["status"] == "ACCEPTED"
        assert result["user_id"] == "alice"
        assert result["message_id"]

        msgs = _drain_queue(aws["queue_url"])
        assert len(msgs) == 1
        assert msgs[0] == {"user_id": "alice", "trigger": "manual"}

    def test_503_when_queue_url_missing(
        self, aws: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.api._errors import HttpError
        from src.api.runs import trigger_run

        monkeypatch.delenv("RUNS_QUEUE_URL", raising=False)
        with pytest.raises(HttpError) as exc:
            trigger_run("alice")
        assert exc.value.status == 503


# -------------------- Worker SQS routing --------------------


class TestWorkerSqsHandling:
    def test_extracts_user_ids_from_sqs_event(self) -> None:
        from src.handler import _extract_user_ids

        event = {
            "Records": [
                {"eventSource": "aws:sqs", "body": json.dumps({"user_id": "alice"})},
                {"eventSource": "aws:sqs", "body": json.dumps({"user_id": "bob"})},
            ]
        }
        assert _extract_user_ids(event) == ["alice", "bob"]

    def test_skips_records_missing_user_id(self) -> None:
        from src.handler import _extract_user_ids

        event = {
            "Records": [
                {"eventSource": "aws:sqs", "body": json.dumps({})},
                {"eventSource": "aws:sqs", "body": json.dumps({"user_id": "carol"})},
            ]
        }
        assert _extract_user_ids(event) == ["carol"]

    def test_handles_direct_user_id_payload(self) -> None:
        from src.handler import _extract_user_ids

        assert _extract_user_ids({"user_id": "dave"}) == ["dave"]

    def test_legacy_events_return_no_user_ids(self) -> None:
        from src.handler import _extract_user_ids

        # EventBridge scheduled event (legacy single-tenant)
        assert _extract_user_ids({"source": "aws.events"}) == []
        # Empty event
        assert _extract_user_ids({}) == []
