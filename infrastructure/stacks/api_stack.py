"""API stack — REST API Gateway + Lambda router + Cognito JWT authorizer.

REST API (not HTTP API) because we need a resource policy for IP allowlist
during development. Cost difference is rounding error at this scale.

Single Lambda routes all requests in-process (cheap, simple, easy to test
locally). Refactor to per-resource Lambdas later if traffic warrants.

Resource policy enforces api_allowed_ips at the edge — requests from outside
return 403 before Cognito even sees them. Empty allowlist → no IP restriction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import (
    aws_apigateway as apigw,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)

from infrastructure.cdk_constructs.lambda_function import _RETENTION_DAYS_MAP

if TYPE_CHECKING:
    from constructs import Construct

    from infrastructure.config import EnvironmentConfig


class ApiStack(Stack):
    """REST API Gateway with Cognito JWT auth + IP allowlist + Lambda router."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        user_pool,
        users_table: dynamodb.ITable,
        favorites_table: dynamodb.ITable,
        runs_table: dynamodb.ITable,
        deals_table: dynamodb.ITable,
        config_table: dynamodb.ITable,
        market_snapshots_table: dynamodb.ITable,
        macro_series_table: dynamodb.ITable,
        api_usage_table: dynamodb.ITable,
        marketcheck_secret: secretsmanager.ISecret,
        afdc_secret: secretsmanager.ISecret,
        worker_lambda_arn: str | None = None,
        runs_queue_url: str | None = None,
        runs_queue_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"lookout-{config.env_name}"

        # --- API Lambda (single router) ---

        # Reuse the project Docker image so env/imports match the worker
        # exactly. CMD is overridden to point at the API handler.
        from pathlib import Path

        from aws_cdk import aws_ecr_assets as ecr_assets

        project_root = Path(__file__).parent.parent.parent

        from aws_cdk import aws_logs as logs

        log_retention = _RETENTION_DAYS_MAP.get(
            config.log_retention_days, logs.RetentionDays.ONE_WEEK
        )
        api_log_group = logs.LogGroup(
            self,
            "ApiLambdaLogs",
            log_group_name=f"/aws/lambda/{prefix}-api",
            retention=log_retention,
            removal_policy=config.removal_policy,
        )

        self.api_lambda = _lambda.DockerImageFunction(
            self,
            "ApiLambda",
            function_name=f"{prefix}-api",
            architecture=_lambda.Architecture.ARM_64,
            code=_lambda.DockerImageCode.from_image_asset(
                str(project_root),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
                cmd=["src.api.handler.handler"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "TEST_MODE": "false",
                "ENVIRONMENT": config.env_name,
                "USERS_TABLE_NAME": users_table.table_name,
                "FAVORITES_TABLE_NAME": favorites_table.table_name,
                "RUNS_TABLE_NAME": runs_table.table_name,
                "DEALS_TABLE_NAME": deals_table.table_name,
                "CONFIG_TABLE_NAME": config_table.table_name,
                "MARKET_SNAPSHOTS_TABLE_NAME": market_snapshots_table.table_name,
                "MACRO_SERIES_TABLE_NAME": macro_series_table.table_name,
                "API_USAGE_TABLE_NAME": api_usage_table.table_name,
                "WORKER_LAMBDA_ARN": worker_lambda_arn or "",
                "RUNS_QUEUE_URL": runs_queue_url or "",
                "AFDC_API_KEY_SECRET_ARN": afdc_secret.secret_arn,
            },
            tracing=_lambda.Tracing.DISABLED,
            description=f"Lookout API ({config.env_name})",
        )

        # --- IAM — least privilege ---

        self.api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                ],
                resources=[
                    users_table.table_arn,
                    f"{users_table.table_arn}/index/*",
                    runs_table.table_arn,
                    f"{runs_table.table_arn}/index/*",
                    deals_table.table_arn,
                    f"{deals_table.table_arn}/index/*",
                    config_table.table_arn,
                    market_snapshots_table.table_arn,
                    f"{market_snapshots_table.table_arn}/index/*",
                    macro_series_table.table_arn,
                    api_usage_table.table_arn,
                ],
            )
        )

        # Favorites table — dedicated statement so DeleteItem is granted ONLY
        # on this table, not silently on users/runs/deals/config. Explicit ARN,
        # no wildcard. No GSI on this table, so no /index/* needed.
        self.api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:Query",
                    "dynamodb:DeleteItem",
                ],
                resources=[favorites_table.table_arn],
            )
        )

        # Read the shared MarketCheck secret (for default-tier user runs)
        # plus the AFDC secret used by the incentive-stack handler.
        self.api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    marketcheck_secret.secret_arn,
                    afdc_secret.secret_arn,
                ],
            )
        )

        # BYOK: per-user MarketCheck secrets named lookout/byok/<user_id>.
        # The API Lambda needs full CRUD; the agent worker only needs read
        # (granted in agent_stack.py). Wildcards are scoped to the prefix —
        # never `*` — so other secrets in the account remain unreachable.
        # Note: Secrets Manager appends a 6-char random suffix to the ARN,
        # so the resource pattern needs the trailing `*`.
        byok_secret_arn_pattern = (
            f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:lookout/byok/*"
        )
        self.api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:CreateSecret",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecret",
                    "secretsmanager:DeleteSecret",
                ],
                resources=[byok_secret_arn_pattern],
            )
        )

        # Logs
        self.api_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    api_log_group.log_group_arn,
                    f"{api_log_group.log_group_arn}:*",
                ],
            )
        )

        # SQS — manual trigger sends a per-user message to the worker queue.
        if runs_queue_arn:
            self.api_lambda.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["sqs:SendMessage"],
                    resources=[runs_queue_arn],
                )
            )

        # --- API Gateway REST API ---

        # Resource policy enforces the IP allowlist at the edge. Empty list
        # disables it (open to everyone subject to Cognito auth).
        policy_document = self._build_resource_policy(config.api_allowed_ips)

        self.api = apigw.RestApi(
            self,
            "Api",
            rest_api_name=f"{prefix}-api",
            description=f"Lookout API ({config.env_name})",
            deploy_options=apigw.StageOptions(
                stage_name=config.env_name,
                throttling_burst_limit=20,
                throttling_rate_limit=10,
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
            ),
            policy=policy_document,
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,  # tighten to dashboard domain in prod
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
            ),
        )

        # --- Cognito JWT authorizer ---

        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self,
            "CognitoAuthorizer",
            cognito_user_pools=[user_pool],
            authorizer_name=f"{prefix}-cognito",
        )

        # --- Routes ---
        # Single Lambda integration, routes resolved in-process via event.path.

        integration = apigw.LambdaIntegration(self.api_lambda, proxy=True)

        auth_kwargs: dict = {
            "authorizer": authorizer,
            "authorization_type": apigw.AuthorizationType.COGNITO,
        }

        def _add(resource: apigw.IResource, method: str) -> None:
            resource.add_method(method, integration, **auth_kwargs)

        # /me
        me = self.api.root.add_resource("me")
        _add(me, "GET")

        # /me/prefs
        prefs = me.add_resource("prefs")
        _add(prefs, "GET")
        _add(prefs, "PUT")

        # /me/runs
        runs = me.add_resource("runs")
        _add(runs, "GET")
        _add(runs, "POST")  # manual trigger

        # /me/runs/in_flight — polled by the dashboard for the progress bar.
        # MUST be declared in API Gateway even though the Lambda's router
        # already handles it; missing routes 403 before the Lambda runs.
        in_flight = runs.add_resource("in_flight")
        _add(in_flight, "GET")

        # /me/incentives — federal + state + utility incentive stack
        incentives = me.add_resource("incentives")
        _add(incentives, "GET")

        # /me/byok-key
        byok_key = me.add_resource("byok-key")
        _add(byok_key, "PUT")
        _add(byok_key, "DELETE")

        # /me/favorites
        favorites = me.add_resource("favorites")
        _add(favorites, "GET")

        # /me/deals
        deals = me.add_resource("deals")
        _add(deals, "GET")

        # /me/deals/{listing_id}/act + /me/deals/{listing_id}/favorite
        deal_item = deals.add_resource("{listing_id}")
        _add(deal_item.add_resource("act"), "POST")
        fav_action = deal_item.add_resource("favorite")
        _add(fav_action, "POST")
        _add(fav_action, "DELETE")

        # --- Outputs ---

        CfnOutput(self, "ApiUrl", value=self.api.url)
        CfnOutput(
            self,
            "AllowedIps",
            value=", ".join(config.api_allowed_ips) if config.api_allowed_ips else "(open)",
        )

    @staticmethod
    def _build_resource_policy(allowed_ips: tuple[str, ...]) -> iam.PolicyDocument | None:
        """Build a resource policy that denies non-allowlisted source IPs.

        Empty allowlist → return None (no policy attached, fully open).
        """
        if not allowed_ips:
            return None

        return iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.AnyPrincipal()],
                    actions=["execute-api:Invoke"],
                    resources=["execute-api:/*/*/*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.DENY,
                    principals=[iam.AnyPrincipal()],
                    actions=["execute-api:Invoke"],
                    resources=["execute-api:/*/*/*"],
                    conditions={"NotIpAddress": {"aws:SourceIp": list(allowed_ips)}},
                ),
            ]
        )
