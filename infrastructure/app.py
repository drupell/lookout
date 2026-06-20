#!/usr/bin/env python3
"""CDK app entry point — instantiates auth, agent, api, and monitoring stacks."""

from __future__ import annotations

import aws_cdk as cdk

from infrastructure.config import DEV, PROD
from infrastructure.stacks.agent_stack import AgentStack
from infrastructure.stacks.api_stack import ApiStack
from infrastructure.stacks.auth_stack import AuthStack
from infrastructure.stacks.dashboard_stack import DashboardStack
from infrastructure.stacks.github_oidc_stack import GitHubOidcStack
from infrastructure.stacks.monitoring_stack import MonitoringStack

app = cdk.App()

US_EAST_1 = cdk.Environment(region="us-east-1")

# --- GitHub OIDC deploy roles (one per account; each deployed separately) ---

GitHubOidcStack(
    app,
    "LookoutDevOidc",
    role_name="lookout-gha-dev",
    subject="repo:drupell/lookout:ref:refs/heads/dev",
    env=US_EAST_1,
    description="GitHub Actions OIDC deploy role — dev account",
)

GitHubOidcStack(
    app,
    "LookoutProdOidc",
    role_name="lookout-gha-prod",
    subject="repo:drupell/lookout:environment:production",
    env=US_EAST_1,
    description="GitHub Actions OIDC deploy role — prod account",
)

# --- Dev Environment ---

dev_dashboard = DashboardStack(
    app,
    "LookoutDevDashboard",
    config=DEV,
    env=US_EAST_1,
    description="Lookout — Dev dashboard hosting (S3 + CloudFront)",
)

dev_auth = AuthStack(
    app,
    "LookoutDevAuth",
    config=DEV,
    dashboard_domain_name=dev_dashboard.distribution.distribution_domain_name,
    env=US_EAST_1,
    description="Lookout — Dev auth (Cognito + Users table)",
)
dev_auth.add_dependency(dev_dashboard)

dev_agent = AgentStack(
    app,
    "LookoutDev",
    config=DEV,
    users_table=dev_auth.users_table,
    env=US_EAST_1,
    description="Lookout — Dev environment",
)
dev_agent.add_dependency(dev_auth)

dev_api = ApiStack(
    app,
    "LookoutDevApi",
    config=DEV,
    user_pool=dev_auth.user_pool,
    users_table=dev_auth.users_table,
    favorites_table=dev_auth.favorites_table,
    runs_table=dev_agent.runs_table,
    deals_table=dev_agent.deals_table,
    config_table=dev_agent.config_table,
    market_snapshots_table=dev_agent.market_snapshots_table,
    macro_series_table=dev_agent.macro_series_table,
    api_usage_table=dev_agent.api_usage_table,
    marketcheck_secret=dev_agent.marketcheck_secret,
    afdc_secret=dev_agent.afdc_secret,
    worker_lambda_arn=dev_agent.lambda_function_arn,
    runs_queue_url=dev_agent.runs_queue_url,
    runs_queue_arn=dev_agent.runs_queue_arn,
    env=US_EAST_1,
    description="Lookout — Dev API (REST + Cognito + IP allowlist)",
)
dev_api.add_dependency(dev_auth)
dev_api.add_dependency(dev_agent)

dev_monitoring = MonitoringStack(
    app,
    "LookoutDevMonitoring",
    config=DEV,
    agent_stack=dev_agent,
    env=US_EAST_1,
    description="Lookout Monitoring — Dev environment",
)

# --- Prod Environment ---

prod_dashboard = DashboardStack(
    app,
    "LookoutProdDashboard",
    config=PROD,
    env=US_EAST_1,
    description="Lookout — Prod dashboard hosting (S3 + CloudFront)",
)

prod_auth = AuthStack(
    app,
    "LookoutProdAuth",
    config=PROD,
    dashboard_domain_name=prod_dashboard.distribution.distribution_domain_name,
    env=US_EAST_1,
    description="Lookout — Prod auth",
)
prod_auth.add_dependency(prod_dashboard)

prod_agent = AgentStack(
    app,
    "LookoutProd",
    config=PROD,
    users_table=prod_auth.users_table,
    env=US_EAST_1,
    description="Lookout — Prod environment",
)
prod_agent.add_dependency(prod_auth)

prod_api = ApiStack(
    app,
    "LookoutProdApi",
    config=PROD,
    user_pool=prod_auth.user_pool,
    users_table=prod_auth.users_table,
    favorites_table=prod_auth.favorites_table,
    runs_table=prod_agent.runs_table,
    deals_table=prod_agent.deals_table,
    config_table=prod_agent.config_table,
    market_snapshots_table=prod_agent.market_snapshots_table,
    macro_series_table=prod_agent.macro_series_table,
    api_usage_table=prod_agent.api_usage_table,
    marketcheck_secret=prod_agent.marketcheck_secret,
    afdc_secret=prod_agent.afdc_secret,
    worker_lambda_arn=prod_agent.lambda_function_arn,
    runs_queue_url=prod_agent.runs_queue_url,
    runs_queue_arn=prod_agent.runs_queue_arn,
    env=US_EAST_1,
    description="Lookout — Prod API",
)
prod_api.add_dependency(prod_auth)
prod_api.add_dependency(prod_agent)

prod_monitoring = MonitoringStack(
    app,
    "LookoutProdMonitoring",
    config=PROD,
    agent_stack=prod_agent,
    env=US_EAST_1,
    description="Lookout Monitoring — Prod environment",
)

app.synth()
