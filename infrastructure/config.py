"""Environment-specific configuration dataclasses for CDK stacks.

Dev and prod are genuinely separated — different removal policies,
timeouts, memory, tracing, and thresholds enforced at the stack level.

Sensitive / per-developer values (like API IP allowlists) come from environment
variables, NOT this file, so they don't end up in version control. See
`.env.example` for the variables this module reads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from aws_cdk import RemovalPolicy


def _csv_env(name: str) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple, skipping blanks."""
    raw = os.environ.get(name, "")
    return tuple(s.strip() for s in raw.split(",") if s.strip())


@dataclass(frozen=True)
class EnvironmentConfig:
    """Configuration for a single deployment environment."""

    env_name: str
    test_mode: bool
    schedule_expression: str
    lambda_timeout_seconds: int
    lambda_memory_mb: int
    removal_policy: RemovalPolicy
    enable_xray_tracing: bool
    log_retention_days: int
    deal_score_threshold: float
    alarm_email: str
    llm_provider: str
    llm_scoring_model: str
    llm_drafting_model: str
    skip_drafting: bool
    # When True, the Lambda wipes the deals table at the start of every run
    # so the table only reflects the latest run. Appropriate for low-cadence
    # operation (weekly). For prod with finer cadence + dedup, leave False.
    refresh_deals_on_run: bool
    # IP allowlist for the API Gateway (CIDR notation). Empty list = no
    # restriction. Used during dev so only the developer's home network can
    # hit the API. In prod, swap for WAF instead.
    api_allowed_ips: tuple[str, ...]
    # Cognito Hosted UI domain prefix (https://<prefix>.auth.<region>.amazoncognito.com).
    # Must be globally unique across Cognito tenants in the region. Override via env
    # if the default collides.
    cognito_domain_prefix: str


DEV = EnvironmentConfig(
    env_name="dev",
    test_mode=False,
    # Hourly: fires the scheduler every hour; per-user schedules drive
    # who actually runs each tick (cadence + time_of_day_utc + start_date).
    schedule_expression="rate(1 hour)",
    lambda_timeout_seconds=300,
    lambda_memory_mb=1024,
    removal_policy=RemovalPolicy.DESTROY,
    enable_xray_tracing=False,
    log_retention_days=7,
    # Lowered while calibrating Nova Lite scoring against real listings.
    # Re-tighten once we see real score distribution and tune to taste.
    deal_score_threshold=0.5,
    alarm_email="",
    llm_provider="bedrock",
    llm_scoring_model="amazon.nova-lite-v1:0",
    # Placeholder until Anthropic models are approved on this account; flip
    # llm_drafting_model + skip_drafting together when ready.
    llm_drafting_model="amazon.nova-lite-v1:0",
    skip_drafting=True,
    refresh_deals_on_run=True,
    # Read from LOOKOUT_DEV_ALLOWED_IPS env var (comma-sep CIDRs).
    # Empty → no restriction (DON'T deploy that way; relies on Cognito alone).
    # Example: "2001:db8::/64,203.0.113.10/32"
    api_allowed_ips=_csv_env("LOOKOUT_DEV_ALLOWED_IPS"),
    cognito_domain_prefix=os.environ.get("LOOKOUT_DEV_COGNITO_DOMAIN_PREFIX", "lookout-dev-auth"),
)

PROD = EnvironmentConfig(
    env_name="prod",
    test_mode=False,
    # Hourly scheduler; per-user schedules pick the actual run time.
    schedule_expression="rate(1 hour)",
    lambda_timeout_seconds=900,
    lambda_memory_mb=2048,
    removal_policy=RemovalPolicy.RETAIN,
    enable_xray_tracing=True,
    log_retention_days=90,
    deal_score_threshold=0.75,
    alarm_email="",
    llm_provider="bedrock",
    llm_scoring_model="amazon.nova-lite-v1:0",
    llm_drafting_model="amazon.nova-lite-v1:0",
    skip_drafting=False,
    # Prod retains historical deals; dedup happens via filter_new_deals.
    refresh_deals_on_run=False,
    # Prod is open-internet by default (relies on Cognito + WAF separately).
    # Override via LOOKOUT_PROD_ALLOWED_IPS if you want to lock it down.
    api_allowed_ips=_csv_env("LOOKOUT_PROD_ALLOWED_IPS"),
    cognito_domain_prefix=os.environ.get("LOOKOUT_PROD_COGNITO_DOMAIN_PREFIX", "lookout-prod-auth"),
)
