#!/usr/bin/env python3
"""CI integration test — validates Lambda response shape.

Usage: python scripts/assert_response.py response.json
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/assert_response.py <response.json>")
        return 1

    with open(sys.argv[1]) as f:
        response = json.load(f)

    # Validate response shape
    assert "statusCode" in response, "Missing statusCode"
    assert response["statusCode"] == 200, f"Expected 200, got {response['statusCode']}"

    body = response.get("body", {})
    required_fields = ["run_id", "status", "environment"]
    for field in required_fields:
        assert field in body, f"Missing required field: {field}"

    assert body["status"] in (
        "SUCCESS",
        "PARTIAL",
        "GUARDRAIL_BLOCKED",
        "ERROR",
    ), f"Invalid status: {body['status']}"

    print(f"Response shape valid. Run: {body['run_id']}, Status: {body['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
