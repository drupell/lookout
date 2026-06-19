"""API Lambda — single entry point that routes API Gateway events.

Cognito JWT auth happens at the API Gateway layer. The Lambda just trusts the
`requestContext.authorizer.claims` block and uses `sub` as the user_id.

Routes (all require auth):
    GET    /me                    → user profile
    GET    /me/prefs              → effective prefs (defaults + user overrides)
    PUT    /me/prefs              → set user overrides (validated against tier)
    GET    /me/runs               → list this user's runs
    POST   /me/runs               → trigger a run for this user (async)
    GET    /me/runs/in_flight     → in-flight run status (for progress bar)
    GET    /me/signal             → market-signal time series + latest point
    GET    /me/calibration        → snapshot history + label summary (90d audit log)
    GET    /me/macro              → macro-series snapshot (auto loan APR, CPI used cars)
    GET    /me/used-vs-new-arbitrage → spread between new vs 1-2yr used for user's primary brand
    GET    /me/inventory-anomaly  → "your matching listing count is unusually thick/thin" verdict
    GET    /me/usage              → current-month API quota utilization
    GET    /me/incentives         → user's qualified federal/state/utility incentive stack
    PUT    /me/byok-key           → submit/rotate a BYOK MarketCheck key
    DELETE /me/byok-key           → remove BYOK key, demote tier to default
    GET    /me/deals              → list this user's deals
    POST   /me/deals/{id}/act     → mark a deal as acted
    GET    /me/favorites          → list this user's favorited deals
    POST   /me/deals/{id}/favorite   → favorite a deal
    DELETE /me/deals/{id}/favorite   → unfavorite a deal

Phase 1A: handlers are stubs. Phase 1B: real implementations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway proxy integration entrypoint."""
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    claims = _extract_claims(event)
    user_id = claims.get("sub", "")
    email = claims.get("email", "")

    logger.info("Request: %s %s by user=%s", method, path, user_id or "(unauthenticated)")

    if not user_id:
        return _response(401, {"error": "unauthenticated"})

    from src.api._errors import HttpError

    try:
        return _route(method, path, event, user_id, email)
    except HttpError as e:
        logger.warning("HttpError %d: %s", e.status, e.message)
        return _response(e.status, e.to_body())
    except Exception as e:
        logger.exception("Unhandled error in API")
        return _response(500, {"error": "internal", "detail": str(e)})


def _route(
    method: str, path: str, event: dict[str, Any], user_id: str, email: str
) -> dict[str, Any]:
    # /me
    if path == "/me" and method == "GET":
        from src.api.users import get_me

        return _response(200, get_me(user_id, email))

    # /me/prefs
    if path == "/me/prefs":
        from src.api.prefs import get_prefs, put_prefs

        if method == "GET":
            return _response(200, get_prefs(user_id))
        if method == "PUT":
            body = _parse_body(event)
            return _response(200, put_prefs(user_id, body))

    # /me/runs
    if path == "/me/runs":
        from src.api.runs import list_runs, trigger_run

        if method == "GET":
            return _response(200, {"runs": list_runs(user_id)})
        if method == "POST":
            return _response(202, trigger_run(user_id))

    # /me/signal
    if path == "/me/signal" and method == "GET":
        from src.api.market_signal import get_market_signal

        qs = event.get("queryStringParameters") or {}
        window = qs.get("window") or "90d"
        view = qs.get("view") or "personalized"
        return _response(200, get_market_signal(user_id, window=window, view=view))

    # /me/calibration
    if path == "/me/calibration" and method == "GET":
        from src.api.calibration import get_calibration

        qs = event.get("queryStringParameters") or {}
        window = qs.get("window") or "90d"
        return _response(200, get_calibration(user_id, window=window))

    # /me/macro
    if path == "/me/macro" and method == "GET":
        from src.api.macro_snapshot import get_macro_snapshot

        return _response(200, get_macro_snapshot(user_id))

    # /me/used-vs-new-arbitrage
    if path == "/me/used-vs-new-arbitrage" and method == "GET":
        from src.api.used_vs_new import get_used_vs_new_arbitrage

        return _response(200, get_used_vs_new_arbitrage(user_id))

    # /me/inventory-anomaly
    if path == "/me/inventory-anomaly" and method == "GET":
        from src.api.inventory_anomaly import get_inventory_anomaly

        return _response(200, get_inventory_anomaly(user_id))

    # /me/usage
    if path == "/me/usage" and method == "GET":
        from src.api.usage import get_usage

        return _response(200, get_usage(user_id))

    # /me/incentives
    if path == "/me/incentives" and method == "GET":
        from src.api.incentives import get_incentives

        return _response(200, get_incentives(user_id))

    # /me/runs/in_flight
    if path == "/me/runs/in_flight" and method == "GET":
        from src.api.runs import get_in_flight_run

        return _response(200, get_in_flight_run(user_id))

    # /me/byok-key
    if path == "/me/byok-key":
        from src.api.byok import delete_byok_key, put_byok_key

        if method == "PUT":
            body = _parse_body(event)
            return _response(200, put_byok_key(user_id, body))
        if method == "DELETE":
            return _response(200, delete_byok_key(user_id))

    # /me/deals
    if path == "/me/deals":
        from src.api.deals import list_deals

        if method == "GET":
            qs = event.get("queryStringParameters") or {}
            return _response(200, {"deals": list_deals(user_id, query=qs)})

    # /me/deals/{listing_id}/act
    if path.startswith("/me/deals/") and path.endswith("/act") and method == "POST":
        from src.api.deals import act_on_deal

        listing_id = (event.get("pathParameters") or {}).get("listing_id", "")
        return _response(200, act_on_deal(user_id, listing_id))

    # /me/favorites
    if path == "/me/favorites" and method == "GET":
        from src.api.favorites import list_favorites

        return _response(200, {"favorites": list_favorites(user_id)})

    # /me/deals/{listing_id}/favorite
    if path.startswith("/me/deals/") and path.endswith("/favorite"):
        from src.api.favorites import add_favorite, remove_favorite

        listing_id = (event.get("pathParameters") or {}).get("listing_id", "")
        if method == "POST":
            note = _parse_body(event).get("note")
            return _response(200, add_favorite(user_id, listing_id, note))
        if method == "DELETE":
            return _response(200, remove_favorite(user_id, listing_id))

    return _response(404, {"error": "not_found", "path": path, "method": method})


def _extract_claims(event: dict[str, Any]) -> dict[str, str]:
    """Pull the Cognito JWT claims from the API Gateway authorizer context."""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    if not isinstance(claims, dict):
        return {}
    return {k: str(v) for k, v in claims.items()}


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def _response(status: int, body: dict[str, Any] | list[Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }
