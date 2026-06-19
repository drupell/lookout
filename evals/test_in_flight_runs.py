"""Tests for in-flight run tracking and the 409 guard on trigger_run.

Covers:
  - mark_running writes a RUNNING row
  - update_progress patches current_node + progress counters
  - get_in_flight returns the active row, ignores stale ones
  - trigger_run rejects with 409 when in-flight, succeeds otherwise
  - get_in_flight_run shapes the response correctly for the API
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

if TYPE_CHECKING:
    from collections.abc import Iterator


RUNS_TABLE = "lookout-test-runs"
QUEUE_NAME = "lookout-test-runs-queue"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Activate moto + provision the runs table + an SQS queue for trigger_run."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("RUNS_TABLE_NAME", RUNS_TABLE)
    monkeypatch.delenv("TEST_MODE", raising=False)
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName=RUNS_TABLE,
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "run_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "user-id-index",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        monkeypatch.setenv("RUNS_QUEUE_URL", queue_url)
        yield


# -------------------- run_state primitives --------------------


class TestRunState:
    def test_mark_running_writes_running_row(self, aws: None) -> None:
        from src.memory import run_state

        started = datetime.now(UTC).isoformat()
        run_state.mark_running("r-1", "u-1", started, "dev")

        item = (
            boto3.resource("dynamodb")
            .Table(RUNS_TABLE)
            .get_item(Key={"run_id": "r-1", "timestamp": started})
            .get("Item")
        )
        assert item is not None
        assert item["status"] == "RUNNING"
        assert item["user_id"] == "u-1"
        assert int(item["progress_completed"]) == 0
        assert int(item["progress_total"]) == run_state.TOTAL_NODES

    def test_update_progress_patches_current_node(self, aws: None) -> None:
        from src.memory import run_state

        started = datetime.now(UTC).isoformat()
        run_state.mark_running("r-2", "u-2", started, "dev")
        run_state.update_progress("r-2", started, "score_deals", 7)

        item = (
            boto3.resource("dynamodb")
            .Table(RUNS_TABLE)
            .get_item(Key={"run_id": "r-2", "timestamp": started})
            .get("Item")
        )
        assert item is not None
        assert item["current_node"] == "score_deals"
        assert int(item["progress_completed"]) == 7

    def test_get_in_flight_returns_active_row(self, aws: None) -> None:
        from src.memory import run_state

        started = datetime.now(UTC).isoformat()
        run_state.mark_running("r-3", "u-3", started, "dev")
        run_state.update_progress("r-3", started, "fetch_listings", 2)

        in_flight = run_state.get_in_flight("u-3")
        assert in_flight is not None
        assert in_flight["run_id"] == "r-3"
        assert in_flight["current_node"] == "fetch_listings"
        assert in_flight["progress_completed"] == 2

    def test_get_in_flight_ignores_stale_rows(self, aws: None) -> None:
        """A RUNNING row older than STALE_RUN_SECONDS is treated as crashed."""
        from src.memory import run_state

        # Write directly with an old started_at to bypass mark_running's now().
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        boto3.resource("dynamodb").Table(RUNS_TABLE).put_item(
            Item={
                "run_id": "r-stale",
                "timestamp": old,
                "started_at": old,
                "status": "RUNNING",
                "user_id": "u-stale",
                "environment": "dev",
                "progress_completed": 1,
                "progress_total": run_state.TOTAL_NODES,
            }
        )
        assert run_state.get_in_flight("u-stale") is None

    def test_get_in_flight_ignores_completed_rows(self, aws: None) -> None:
        from src.memory import run_state

        boto3.resource("dynamodb").Table(RUNS_TABLE).put_item(
            Item={
                "run_id": "r-done",
                "timestamp": datetime.now(UTC).isoformat(),
                "status": "SUCCESS",
                "user_id": "u-done",
                "environment": "dev",
            }
        )
        assert run_state.get_in_flight("u-done") is None


# -------------------- trigger_run + 409 --------------------


class TestTriggerRunGuard:
    def test_succeeds_when_no_in_flight_run(self, aws: None) -> None:
        from src.api.runs import trigger_run

        result = trigger_run("u-fresh")
        assert result["status"] == "ACCEPTED"

    def test_returns_409_when_run_in_flight(self, aws: None) -> None:
        from src.api._errors import HttpError
        from src.api.runs import trigger_run
        from src.memory import run_state

        run_state.mark_running("r-active", "u-active", datetime.now(UTC).isoformat(), "dev")

        with pytest.raises(HttpError) as exc:
            trigger_run("u-active")
        assert exc.value.status == 409
        assert exc.value.details and exc.value.details.get("run_id") == "r-active"

    def test_succeeds_when_only_stale_in_flight_rows(self, aws: None) -> None:
        from src.api.runs import trigger_run
        from src.memory import run_state

        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        boto3.resource("dynamodb").Table(RUNS_TABLE).put_item(
            Item={
                "run_id": "r-stale",
                "timestamp": old,
                "started_at": old,
                "status": "RUNNING",
                "user_id": "u-stale-trigger",
                "environment": "dev",
                "progress_completed": 1,
                "progress_total": run_state.TOTAL_NODES,
            }
        )
        result = trigger_run("u-stale-trigger")
        assert result["status"] == "ACCEPTED"


# -------------------- get_in_flight_run shaping --------------------


class TestGetInFlightRunShape:
    def test_no_run_returns_in_flight_false(self, aws: None) -> None:
        from src.api.runs import get_in_flight_run

        assert get_in_flight_run("u-none") == {"in_flight": False}

    def test_active_run_includes_progress(self, aws: None) -> None:
        from src.api.runs import get_in_flight_run
        from src.memory import run_state

        started = datetime.now(UTC).isoformat()
        run_state.mark_running("r-shape", "u-shape", started, "dev")
        run_state.update_progress("r-shape", started, "score_deals", 7)

        result = get_in_flight_run("u-shape")
        assert result["in_flight"] is True
        assert result["run_id"] == "r-shape"
        assert result["current_node"] == "score_deals"
        assert result["progress"] == {"completed": 7, "total": run_state.TOTAL_NODES}
