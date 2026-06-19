"""Lookout dashboard — Streamlit UI for browsing runs, deals, and triggering runs.

Run locally:
    AWS_PROFILE=lookout-dev streamlit run src/dashboard/app.py

Reads live from DynamoDB. Set LOOKOUT_ENV to switch environments (default: dev).
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# Streamlit runs this file as a script, not as part of the package. Make the
# project root importable so `from src.config.loader import ...` works whether
# the user launches via `make dashboard` or `streamlit run src/dashboard/app.py`.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import boto3  # noqa: E402
import streamlit as st  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

ENV = os.environ.get("LOOKOUT_ENV", "dev")
RUNS_TABLE = f"lookout-{ENV}-runs"
DEALS_TABLE = f"lookout-{ENV}-deals"
TRADE_IN_TABLE = f"lookout-{ENV}-trade-in"
CONFIG_TABLE = f"lookout-{ENV}-config"
LAMBDA_NAME = f"lookout-{ENV}"


@st.cache_resource
def _dynamodb():
    return boto3.resource("dynamodb")


@st.cache_resource
def _lambda():
    return boto3.client("lambda")


def _to_native(obj: Any) -> Any:
    """DynamoDB returns Decimals — convert for display + JSON serialization."""
    if isinstance(obj, list):
        return [_to_native(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


@st.cache_data(ttl=30)
def fetch_runs(limit: int = 50) -> list[dict[str, Any]]:
    table = _dynamodb().Table(RUNS_TABLE)
    resp = table.scan(Limit=limit)
    items = sorted(resp.get("Items", []), key=lambda x: x.get("timestamp", ""), reverse=True)
    return _to_native(items)


@st.cache_data(ttl=30)
def fetch_deals(status_filter: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    table = _dynamodb().Table(DEALS_TABLE)
    if status_filter:
        resp = table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq(status_filter),
            ScanIndexForward=False,
            Limit=limit,
        )
    else:
        resp = table.scan(Limit=limit)
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("first_seen", ""), reverse=True)
    return _to_native(items)


def trigger_run() -> dict[str, Any]:
    payload = json.dumps(
        {"source": "manual", "detail-type": "Manual Trigger", "detail": {}}
    ).encode()
    resp = _lambda().invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=payload,
    )
    body = json.loads(resp["Payload"].read())
    return {"status_code": resp["StatusCode"], "body": body}


def mark_acted(listing_id: str, first_seen: str) -> None:
    table = _dynamodb().Table(DEALS_TABLE)
    table.update_item(
        Key={"listing_id": listing_id, "first_seen": first_seen},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "ACTED"},
    )


@st.cache_data(ttl=10)
def fetch_config_overrides() -> dict[str, Any]:
    """Read the active config overrides from DynamoDB, or {} if none."""
    table = _dynamodb().Table(CONFIG_TABLE)
    try:
        resp = table.get_item(Key={"key": "active"})
    except Exception:
        return {}
    item = resp.get("Item") or {}
    overrides = item.get("overrides") or {}
    return _to_native(overrides)


def save_config_overrides(overrides: dict[str, Any]) -> None:
    """Write a complete overrides payload to the active config item."""
    table = _dynamodb().Table(CONFIG_TABLE)
    table.put_item(
        Item={
            "key": "active",
            "overrides": _floats_to_decimals(overrides),
            "updated_at": _now_iso(),
        }
    )


def _floats_to_decimals(obj: Any) -> Any:
    """DynamoDB rejects floats — convert them to Decimal."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimals(v) for v in obj]
    return obj


def _now_iso() -> str:
    from datetime import datetime as _dt

    return _dt.utcnow().isoformat() + "Z"


# --- UI ---


@st.cache_resource
def _prefs():
    """Read preferences.yaml once per session for header context."""
    from src.config.loader import load_preferences

    return load_preferences()


st.set_page_config(page_title="Lookout", layout="wide", page_icon=":telescope:")
st.title("Lookout")

_p = _prefs()
st.caption(
    f"Env: `{ENV}`  |  Lambda: `{LAMBDA_NAME}`  |  "
    f"Search: zip **{_p.search.location_zip}**, "
    f"radius **{_p.search.radius_miles} mi**, "
    f"max age **{_p.search.max_vehicle_age_years}y**, "
    f"fuel `{', '.join(_p.search.fuel_types) or 'any'}`"
)

with st.sidebar:
    st.header("Controls")
    if st.button("Trigger Run", use_container_width=True, type="primary"):
        with st.spinner("Invoking Lambda — this takes ~20s..."):
            try:
                result = trigger_run()
                if result["status_code"] == 200:
                    body = result["body"].get("body", {})
                    st.success(f"Run complete. Status: {body.get('status', 'unknown')}")
                else:
                    st.error(f"Lambda returned status {result['status_code']}")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Failed: {e}")

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Profile must have InvokeFunction + DynamoDB read/update on lookout-* resources.")

tab_runs, tab_deals, tab_settings = st.tabs(["Runs", "Deals", "Settings"])

with tab_runs:
    st.subheader("Recent runs")
    runs = fetch_runs()
    if not runs:
        st.info(
            "No runs yet. Trigger one from the sidebar"
            " (requires TEST_MODE=false on Lambda to write to DynamoDB)."
        )
    else:
        st.dataframe(
            [
                {
                    "Run ID": r["run_id"][:8],
                    "Timestamp": r.get("timestamp", "")[:19],
                    "Status": r.get("status", ""),
                    "Deals Found": r.get("deals_found", 0),
                    "Above Threshold": r.get("deals_above_threshold", 0),
                    "Drafts": r.get("drafts_produced", 0),
                    "Guardrails": len(r.get("guardrail_triggers", [])),
                }
                for r in runs
            ],
            use_container_width=True,
            hide_index=True,
        )

with tab_deals:
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        min_score = st.slider(
            "Minimum overall score",
            min_value=0.0,
            max_value=1.0,
            value=0.5,
            step=0.05,
            help="Filter to deals at or above this score. The Lambda persists ALL "
            "scored deals so you can recalibrate without redeploying.",
        )
    with col2:
        status = st.selectbox("Status", ["All", "NEW", "NOTIFIED", "ACTED"])
    with col3:
        only_above_threshold = st.checkbox(
            "Lambda's threshold only",
            value=False,
            help="Show only deals the Lambda flagged as above threshold at write time.",
        )

    all_deals = fetch_deals(status_filter=status if status != "All" else None)

    deals = [d for d in all_deals if d.get("overall_score", 0) >= min_score]
    if only_above_threshold:
        deals = [d for d in deals if d.get("above_threshold", False)]

    deals.sort(key=lambda d: d.get("overall_score", 0), reverse=True)

    # Distance distribution — quick sanity check on radius coverage
    if all_deals:
        bands = {"0-10mi": 0, "10-25mi": 0, "25-50mi": 0, "50+ mi": 0}
        for d in all_deals:
            dist = d.get("dealer_distance_miles", 0) or 0
            if dist < 10:
                bands["0-10mi"] += 1
            elif dist < 25:
                bands["10-25mi"] += 1
            elif dist < 50:
                bands["25-50mi"] += 1
            else:
                bands["50+ mi"] += 1
        max_dist = max((d.get("dealer_distance_miles", 0) or 0) for d in all_deals)
        dist_summary = "  ".join(f"{k}: {v}" for k, v in bands.items())
        st.caption(f"Distance spread (n={len(all_deals)}, max={max_dist:.0f}mi):  {dist_summary}")

    st.caption(f"Showing {len(deals)} of {len(all_deals)} total deals")

    if not deals:
        st.info("No deals match the current filters. Try lowering the score slider.")
    else:

        def _label(d: dict[str, Any]) -> str:
            score = d.get("overall_score", 0) or 0
            year = int(d.get("year") or 0) or "—"
            make = d.get("make") or "—"
            model = d.get("model") or "—"
            trim = d.get("trim") or ""
            mileage = int(d.get("mileage") or 0)
            price = d.get("selling_price") or 0
            star = " ★" if d.get("above_threshold") else ""
            mileage_part = f"{mileage:,} mi  |  " if mileage else ""
            return (
                f"{round(score * 100)}%  {year} {make} {model} {trim}".rstrip()
                + f"  |  {mileage_part}${price:,.0f}{star}"
            )

        labels = [_label(d) for d in deals]
        idx = st.selectbox("Select deal", range(len(deals)), format_func=lambda i: labels[i])
        deal = deals[idx]

        # --- Detail view ---
        st.divider()

        title_year = int(deal.get("year") or 0) or "—"
        title_make = deal.get("make") or "—"
        title_model = deal.get("model") or "—"
        title_trim = deal.get("trim") or ""
        st.subheader(f"{title_year} {title_make} {title_model} {title_trim}".rstrip())

        # Top metrics row
        msrp = deal.get("msrp") or 0
        selling_price = deal.get("selling_price") or 0
        oop = deal.get("effective_out_of_pocket_usd") or selling_price
        savings = (msrp - selling_price) if (msrp and selling_price) else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Selling price", f"${selling_price:,.0f}")
        if msrp:
            c2.metric(
                "MSRP",
                f"${msrp:,.0f}",
                f"-${savings:,.0f}" if savings > 0 else None,
            )
        else:
            c2.metric("MSRP", "—")
        c3.metric("Effective OOP", f"${oop:,.0f}")
        c4.metric("Score", f"{deal.get('overall_score', 0):.2f}")

        # Vehicle + dealer info row
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Vehicle**")
            mileage = int(deal.get("mileage") or 0)
            distance = deal.get("dealer_distance_miles", 0) or 0
            fuel = deal.get("fuel_type") or "—"
            powertrain = deal.get("powertrain_type") or "—"
            body = deal.get("body_type") or "—"
            st.write(f"Fuel type: `{fuel}`  |  Powertrain: `{powertrain}`  |  Body: `{body}`")
            mileage_str = f"{mileage:,} mi" if mileage else "—"
            st.write(f"Mileage: {mileage_str}  |  Dealer distance: {distance:.0f}mi")
        with c2:
            st.markdown("**Dealer & status**")
            st.write(f"Dealer: {deal.get('dealer_name', 'unknown')}")
            st.write(
                f"Status: `{deal.get('status', '')}`"
                + ("  ★ above threshold" if deal.get("above_threshold") else "")
            )

        # Reasoning — surface this prominently, it's the most useful field
        breakdown = deal.get("score_breakdown") or {}
        reasoning = breakdown.get("reasoning")
        if reasoning:
            st.markdown("**Why this score**")
            st.info(reasoning)

        # Action: open dealer listing
        url = deal.get("url") or ""
        if url:
            st.link_button("Open listing on dealer site", url, type="primary")
        else:
            st.caption("No URL available for this listing.")

        # Drill-down sections
        with st.expander("Score breakdown"):
            st.json({k: v for k, v in breakdown.items() if k != "reasoning"})

        with st.expander("Applicable incentives"):
            incentives = deal.get("applicable_incentives") or []
            if incentives:
                st.json(incentives)
            else:
                st.caption("(none)")

        with st.expander("Trade-in value used at scoring"):
            st.write(f"${deal.get('trade_in_value_at_scoring', 0):,.0f}")

        # Expiration (DynamoDB TTL)
        expires_at = deal.get("expires_at")
        if expires_at:
            from datetime import datetime as _dt

            exp_dt = _dt.fromtimestamp(int(expires_at))
            st.caption(f"Auto-expires: {exp_dt.strftime('%Y-%m-%d')}  (DynamoDB TTL)")

        with st.expander("Raw record"):
            st.json(deal)

        # Mark-as-acted action
        st.divider()
        if deal.get("status") != "ACTED":
            if st.button("Mark as ACTED", type="primary"):
                mark_acted(deal["listing_id"], deal["first_seen"])
                st.cache_data.clear()
                st.success("Marked as ACTED")
                st.rerun()
        else:
            st.caption("Already marked ACTED")

with tab_settings:
    st.subheader("Live preferences")
    st.caption(
        "Edits write to the ConfigTable and merge on top of `preferences.yaml` at "
        "the start of the next Lambda run. No redeploy needed. Each run snapshots "
        "the active config into its audit record."
    )

    overrides = fetch_config_overrides()
    has_overrides = bool(overrides)

    if has_overrides:
        st.success("Overrides currently active. Lambda is using YAML defaults + overrides below.")
    else:
        st.info("No overrides set. Lambda is using bundled `preferences.yaml` defaults as-is.")

    # Use the active *merged* prefs as form defaults so the user sees current state.
    current = _prefs()
    cur_search = current.search

    with st.form("prefs_form"):
        st.markdown("### Search")
        c1, c2, c3 = st.columns(3)
        zip_code = c1.text_input("Zip code", value=cur_search.location_zip)
        radius = c2.number_input(
            "Radius (mi)", min_value=1, max_value=500, value=cur_search.radius_miles
        )
        max_age = c3.number_input(
            "Max vehicle age (years)",
            min_value=1,
            max_value=10,
            value=cur_search.max_vehicle_age_years,
        )

        c1, c2 = st.columns(2)
        min_price = c1.number_input(
            "Min price (USD, 0 = no bound)",
            min_value=0,
            max_value=500000,
            value=int(cur_search.min_price_usd),
            step=1000,
        )
        max_price = c2.number_input(
            "Max price (USD, 0 = no bound)",
            min_value=0,
            max_value=500000,
            value=int(cur_search.max_price_usd),
            step=1000,
        )

        c1, c2 = st.columns(2)
        target_listings = c1.number_input(
            "Target listings per run",
            min_value=1,
            max_value=2000,
            value=cur_search.target_listings,
            step=10,
        )
        max_pages = c2.number_input(
            "Max pages per run (1 page = 50 listings = 1 API call)",
            min_value=1,
            max_value=100,
            value=cur_search.max_pages,
        )

        fuel_types_str = st.text_input(
            "Fuel types (comma-separated, blank = all)",
            value=", ".join(cur_search.fuel_types),
            help='Examples: "Electric", "Electric, Plug-in Hybrid"',
        )

        st.markdown("### Brands")
        c1, c2 = st.columns(2)
        included_brands_str = c1.text_input(
            "Whitelist (comma-separated, blank = all brands)",
            value=", ".join(current.included_brands),
        )
        excluded_brands_str = c2.text_input(
            "Blacklist (comma-separated)",
            value=", ".join(current.excluded_brands),
        )

        excluded_models_str = st.text_input(
            "Excluded models (comma-separated)",
            value=", ".join(current.excluded_models),
        )

        st.markdown("### Scoring thresholds")
        c1, c2 = st.columns(2)
        threshold_notify = c1.slider(
            "Notify threshold (above_threshold flag)",
            min_value=0.0,
            max_value=1.0,
            value=float(current.scoring.threshold_notify),
            step=0.05,
        )
        threshold_draft = c2.slider(
            "Draft email threshold",
            min_value=0.0,
            max_value=1.0,
            value=float(current.scoring.threshold_draft_email),
            step=0.05,
        )

        col_save, col_clear = st.columns([1, 1])
        save_clicked = col_save.form_submit_button("Save overrides", type="primary")
        clear_clicked = col_clear.form_submit_button("Clear all overrides", type="secondary")

    if save_clicked:

        def _csv(s: str) -> list[str]:
            return [x.strip() for x in s.split(",") if x.strip()]

        new_overrides = {
            "search": {
                "location_zip": zip_code,
                "radius_miles": int(radius),
                "max_vehicle_age_years": int(max_age),
                "fuel_types": _csv(fuel_types_str),
                "min_price_usd": int(min_price),
                "max_price_usd": int(max_price),
                "target_listings": int(target_listings),
                "max_pages": int(max_pages),
            },
            "included_brands": _csv(included_brands_str),
            "excluded_brands": _csv(excluded_brands_str),
            "excluded_models": _csv(excluded_models_str),
            "scoring": {
                "threshold_notify": float(threshold_notify),
                "threshold_draft_email": float(threshold_draft),
            },
        }
        try:
            save_config_overrides(new_overrides)
            st.cache_data.clear()
            st.cache_resource.clear()
            st.success("Saved. Next Lambda run will use these overrides.")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed (need write access on lookout-{ENV}-config): {e}")

    if clear_clicked:
        try:
            save_config_overrides({})
            st.cache_data.clear()
            st.cache_resource.clear()
            st.success("Cleared. Next Lambda run will use bundled defaults only.")
            st.rerun()
        except Exception as e:
            st.error(f"Clear failed: {e}")

    st.divider()
    with st.expander("Currently active overrides (raw)"):
        st.json(overrides if overrides else {"(none)": "using bundled defaults"})
