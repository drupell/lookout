"""Agent stack — Worker Lambda, Scheduler Lambda, SQS, DynamoDB, Secrets, IAM.

Architecture:
    EventBridge ──cron──► Scheduler Lambda ──per-user msg──► SQS Queue
                                                                  │
                                                                  ▼
                                                          Worker Lambda
                                                       (one invocation
                                                          per user_id)

All IAM policies use explicit ARNs. No wildcard resources anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_lambda_event_sources as lambda_event_sources,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_sqs as sqs,
)

from infrastructure.cdk_constructs.lambda_function import (
    _RETENTION_DAYS_MAP,
    AgentLambdaFunction,
)

if TYPE_CHECKING:
    from constructs import Construct

    from infrastructure.config import EnvironmentConfig


class AgentStack(Stack):
    """Core infrastructure stack for the Lookout agent."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        users_table: dynamodb.ITable | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = config
        prefix = f"lookout-{config.env_name}"

        # --- DynamoDB Tables ---

        self.runs_table = dynamodb.Table(
            self,
            "RunsTable",
            table_name=f"{prefix}-runs",
            partition_key=dynamodb.Attribute(name="run_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )
        # GSI for querying runs by environment and status
        self.runs_table.add_global_secondary_index(
            index_name="env-status-index",
            partition_key=dynamodb.Attribute(
                name="environment", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
        )
        # GSI for per-user run history
        self.runs_table.add_global_secondary_index(
            index_name="user-id-index",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
        )

        self.deals_table = dynamodb.Table(
            self,
            "DealsTable",
            table_name=f"{prefix}-deals",
            partition_key=dynamodb.Attribute(name="listing_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="first_seen", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            # Auto-expire deal records (90-day TTL set in src/memory/deal_store.py).
            # Bounds storage and keeps us in line with MarketCheck ToU.
            time_to_live_attribute="expires_at",
        )
        # GSI for querying deals by status
        self.deals_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="first_seen", type=dynamodb.AttributeType.STRING),
        )
        # GSI for querying deals by run_id
        self.deals_table.add_global_secondary_index(
            index_name="run-id-index",
            partition_key=dynamodb.Attribute(name="run_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="first_seen", type=dynamodb.AttributeType.STRING),
        )
        # GSI for per-user deal listing — sorted by score for "best deals first" reads
        self.deals_table.add_global_secondary_index(
            index_name="user-id-index",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="first_seen", type=dynamodb.AttributeType.STRING),
        )

        self.trade_in_table = dynamodb.Table(
            self,
            "TradeInTable",
            table_name=f"{prefix}-trade-in",
            partition_key=dynamodb.Attribute(name="vin", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="date", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # Live preferences overrides — single-item table keyed by `key="active"`.
        # Lambda merges this on top of bundled preferences.yaml at run start.
        # Dashboard writes to it. Empty/missing → bundled defaults are used.
        self.config_table = dynamodb.Table(
            self,
            "ConfigTable",
            table_name=f"{prefix}-config",
            partition_key=dynamodb.Attribute(name="key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # --- Market-signal feature tables ---
        # Per-run derived market signal: composite index, factor contributions,
        # variance, flower position. Tiny rows (one per run) so we keep them
        # indefinitely — they power chart windows up to 24 months. PK is either
        # a user_id (personalized signal) or "market:zip:<zip>" (synthetic
        # per-zip "market user" signal); the GSI lets us look up all
        # market-snapshot rows for a given zip across users.
        self.market_snapshots_table = dynamodb.Table(
            self,
            "MarketSnapshotsTable",
            table_name=f"{prefix}-market-snapshots",
            partition_key=dynamodb.Attribute(
                name="snapshot_key", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )
        self.market_snapshots_table.add_global_secondary_index(
            index_name="zip-code-index",
            partition_key=dynamodb.Attribute(name="zip_code", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
        )

        # External macro series cache — FRED economic indicators + the monthly
        # Manheim Used Vehicle Value Index. PK is "<provider>:<series_id>"
        # (e.g. "fred:TERMCBAUTO48NS" or "manheim:headline"); SK is the
        # ISO observation timestamp. Tiny rows; refreshed by scheduled crons,
        # read by the persist_market_snapshot node when composing the Macro
        # index. Surviving a brief provider outage by falling back to the most
        # recent cached row is the whole point — see "cross-source resilience"
        # in the design doc.
        self.macro_series_table = dynamodb.Table(
            self,
            "MacroSeriesTable",
            table_name=f"{prefix}-macro-series",
            partition_key=dynamodb.Attribute(name="series_key", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # API quota tracker — per-key monthly call counts. Used to surface
        # MarketCheck free-tier utilization to the user before they get
        # blocked. PK is the api_key_id ("shared" for the default-tier shared
        # key, "byok:<user_id>" for per-user keys); SK is "YYYY-MM".
        self.api_usage_table = dynamodb.Table(
            self,
            "ApiUsageTable",
            table_name=f"{prefix}-api-usage",
            partition_key=dynamodb.Attribute(name="api_key_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="yyyy_mm", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
        )

        # --- Secrets Manager ---

        # MarketCheck API key. Seed with:
        # aws secretsmanager put-secret-value \\
        #   --secret-id lookout/<env>/marketcheck \\
        #   --secret-string '{"api_key": "..."}'
        self.marketcheck_secret = secretsmanager.Secret(
            self,
            "MarketCheckSecret",
            secret_name=f"lookout/{config.env_name}/marketcheck",
            description=f"MarketCheck API key ({config.env_name})",
        )

        # FRED API key — used by the nightly macro-series refresh cron to pull
        # auto loan rates (TERMCBAUTO48NS) and used-car CPI (CUUR0000SETA02).
        # Free tier; seed once with:
        # aws secretsmanager put-secret-value \\
        #   --secret-id lookout/<env>/fred \\
        #   --secret-string '{"api_key": "..."}'
        self.fred_secret = secretsmanager.Secret(
            self,
            "FredSecret",
            secret_name=f"lookout/{config.env_name}/fred",
            description=f"FRED API key ({config.env_name})",
        )

        # auto.dev API key — listings, VIN decode, wholesale data for implied
        # residuals. Free tier (1000 calls/mo); seed once with:
        # aws secretsmanager put-secret-value \\
        #   --secret-id lookout/<env>/autodev \\
        #   --secret-string '{"api_key": "..."}'
        self.autodev_secret = secretsmanager.Secret(
            self,
            "AutoDevSecret",
            secret_name=f"lookout/{config.env_name}/autodev",
            description=f"auto.dev API key ({config.env_name})",
        )

        # DOE AFDC API key — federal + state + utility incentive programs for
        # the incentive-stack calculator. Free tier (1000 req/hr); seed once
        # with:
        # aws secretsmanager put-secret-value \\
        #   --secret-id lookout/<env>/afdc \\
        #   --secret-string '{"api_key": "..."}'
        # Get a key at https://developer.nrel.gov/signup/
        self.afdc_secret = secretsmanager.Secret(
            self,
            "AfdcSecret",
            secret_name=f"lookout/{config.env_name}/afdc",
            description=f"DOE AFDC API key ({config.env_name})",
        )

        # --- Lambda Function ---

        self.agent_lambda = AgentLambdaFunction(self, "AgentLambda", config=config)

        # Add table name env vars to Lambda
        self.agent_lambda.function.add_environment("RUNS_TABLE_NAME", self.runs_table.table_name)
        self.agent_lambda.function.add_environment("DEALS_TABLE_NAME", self.deals_table.table_name)
        self.agent_lambda.function.add_environment(
            "TRADE_IN_TABLE_NAME", self.trade_in_table.table_name
        )
        self.agent_lambda.function.add_environment(
            "MARKETCHECK_API_KEY_SECRET_ARN", self.marketcheck_secret.secret_arn
        )
        # Trade-in scrapers (KBB, CarMax) require a chromium binary that isn't
        # in the default Lambda runtime. Short-circuit them so they return None
        # silently instead of stack-tracing on every run. Re-enable when we
        # ship a Lambda layer or container image with the browser baked in.
        self.agent_lambda.function.add_environment("DISABLE_TRADE_IN_SCRAPERS", "true")
        self.agent_lambda.function.add_environment(
            "CONFIG_TABLE_NAME", self.config_table.table_name
        )
        self.agent_lambda.function.add_environment(
            "MARKET_SNAPSHOTS_TABLE_NAME", self.market_snapshots_table.table_name
        )
        self.agent_lambda.function.add_environment(
            "MACRO_SERIES_TABLE_NAME", self.macro_series_table.table_name
        )
        self.agent_lambda.function.add_environment(
            "API_USAGE_TABLE_NAME", self.api_usage_table.table_name
        )
        self.agent_lambda.function.add_environment(
            "FRED_API_KEY_SECRET_ARN", self.fred_secret.secret_arn
        )
        self.agent_lambda.function.add_environment(
            "AUTODEV_API_KEY_SECRET_ARN", self.autodev_secret.secret_arn
        )
        self.agent_lambda.function.add_environment(
            "AFDC_API_KEY_SECRET_ARN", self.afdc_secret.secret_arn
        )

        # --- IAM — Least Privilege ---

        # DynamoDB permissions — explicit actions, scoped to specific table ARNs
        self.agent_lambda.function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchWriteItem",
                ],
                resources=[
                    self.runs_table.table_arn,
                    f"{self.runs_table.table_arn}/index/*",
                    self.deals_table.table_arn,
                    f"{self.deals_table.table_arn}/index/*",
                    self.trade_in_table.table_arn,
                    f"{self.trade_in_table.table_arn}/index/*",
                    self.config_table.table_arn,
                    self.market_snapshots_table.table_arn,
                    f"{self.market_snapshots_table.table_arn}/index/*",
                    self.macro_series_table.table_arn,
                    self.api_usage_table.table_arn,
                ],
            )
        )

        # Secrets Manager — read shared default-tier key + per-user BYOK keys
        # + the FRED key for macro-series refresh. BYOK secrets are named
        # lookout/byok/<user_id>; the trailing wildcard accommodates the random
        # 6-char suffix Secrets Manager appends to ARNs.
        byok_secret_arn_pattern = (
            f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:lookout/byok/*"
        )
        self.agent_lambda.function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                ],
                resources=[
                    self.marketcheck_secret.secret_arn,
                    self.fred_secret.secret_arn,
                    self.autodev_secret.secret_arn,
                    self.afdc_secret.secret_arn,
                    byok_secret_arn_pattern,
                ],
            )
        )

        # CloudWatch Logs — scoped to specific log group
        self.agent_lambda.function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    self.agent_lambda.log_group.log_group_arn,
                    f"{self.agent_lambda.log_group.log_group_arn}:*",
                ],
            )
        )

        # Bedrock — invoke scoped to the specific models this env is configured to use.
        # Deduped so the same model referenced in both scoring + drafting only appears once.
        bedrock_model_ids = sorted({config.llm_scoring_model, config.llm_drafting_model})
        self.agent_lambda.function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/{mid}"
                    for mid in bedrock_model_ids
                ],
            )
        )

        # X-Ray tracing (only if enabled)
        # We set tracing=DISABLED in the construct to avoid CDK auto-generating
        # a Resource=* IAM policy. Instead we configure tracing via CfnFunction
        # override and add the IAM policy with a scoped ARN pattern.
        if config.enable_xray_tracing:
            cfn_function = self.agent_lambda.function.node.default_child
            cfn_function.add_property_override("TracingConfig", {"Mode": "Active"})
            self.agent_lambda.function.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                    ],
                    resources=[
                        f"arn:aws:xray:{self.region}:{self.account}:group/*",
                        f"arn:aws:xray:{self.region}:{self.account}:sampling-rule/*",
                    ],
                )
            )

        # --- SQS — per-user run queue ---

        # Worker timeout drives queue visibility (must be > worker timeout x 6).
        # Dead-letter queue catches messages that fail repeatedly (poison).
        self.runs_dlq = sqs.Queue(
            self,
            "RunsDLQ",
            queue_name=f"{prefix}-runs-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(14),
            removal_policy=config.removal_policy,
        )
        self.runs_queue = sqs.Queue(
            self,
            "RunsQueue",
            queue_name=f"{prefix}-runs",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            visibility_timeout=Duration.seconds(config.lambda_timeout_seconds * 6),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=2, queue=self.runs_dlq),
            removal_policy=config.removal_policy,
        )

        # Worker consumes one message at a time so a slow user doesn't block others.
        # Concurrency naturally limited by Lambda's reserved-concurrency settings.
        self.agent_lambda.function.add_event_source(
            lambda_event_sources.SqsEventSource(
                self.runs_queue,
                batch_size=1,
                report_batch_item_failures=True,
            )
        )

        # Worker also needs the Users table at runtime to load per-user prefs
        # and to stamp `last_run_at` after a successful run (so the dashboard's
        # stale-settings banner can compare prefs.updated_at vs last_run_at).
        if users_table is not None:
            self.agent_lambda.function.add_environment("USERS_TABLE_NAME", users_table.table_name)
            self.agent_lambda.function.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["dynamodb:GetItem", "dynamodb:UpdateItem"],
                    resources=[users_table.table_arn],
                )
            )

        # --- Scheduler Lambda — fans out to SQS on cron ---

        scheduler_log_group = logs.LogGroup(
            self,
            "SchedulerLambdaLogs",
            log_group_name=f"/aws/lambda/{prefix}-scheduler",
            retention=_RETENTION_DAYS_MAP.get(
                config.log_retention_days, logs.RetentionDays.ONE_WEEK
            ),
            removal_policy=config.removal_policy,
        )

        project_root = Path(__file__).parent.parent.parent
        self.scheduler_lambda = _lambda.DockerImageFunction(
            self,
            "SchedulerLambda",
            function_name=f"{prefix}-scheduler",
            architecture=_lambda.Architecture.ARM_64,
            code=_lambda.DockerImageCode.from_image_asset(
                str(project_root),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
                cmd=["src.scheduler.handler.handler"],
            ),
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "ENVIRONMENT": config.env_name,
                "RUNS_QUEUE_URL": self.runs_queue.queue_url,
                "USERS_TABLE_NAME": users_table.table_name if users_table else "",
            },
            tracing=_lambda.Tracing.DISABLED,
            description=f"Lookout scheduler — fans out per-user runs ({config.env_name})",
        )
        self.scheduler_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    scheduler_log_group.log_group_arn,
                    f"{scheduler_log_group.log_group_arn}:*",
                ],
            )
        )
        self.scheduler_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sqs:SendMessage"],
                resources=[self.runs_queue.queue_arn],
            )
        )
        if users_table is not None:
            self.scheduler_lambda.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "dynamodb:Scan",
                        "dynamodb:GetItem",  # next_run_at lookup
                        "dynamodb:UpdateItem",  # advance next_run_at after queuing
                    ],
                    resources=[users_table.table_arn],
                )
            )

        # --- EventBridge Schedule (now points at scheduler) ---

        self.schedule_rule = events.Rule(
            self,
            "ScheduleRule",
            rule_name=f"{prefix}-schedule",
            schedule=events.Schedule.expression(config.schedule_expression),
            description=f"Lookout scheduled run ({config.env_name})",
            enabled=True,
        )
        self.schedule_rule.add_target(targets.LambdaFunction(self.scheduler_lambda))

        # --- Macro-series refresh Lambda — nightly cron pulling FRED + Manheim + AFDC ---
        # Same Docker image as the worker (so the FRED / Manheim / AFDC clients
        # and macro_series_store are already in the bundle); CMD points at the
        # cron-specific entry point in src/handler.py.

        macro_refresh_log_group = logs.LogGroup(
            self,
            "MacroRefreshLambdaLogs",
            log_group_name=f"/aws/lambda/{prefix}-macro-refresh",
            retention=_RETENTION_DAYS_MAP.get(
                config.log_retention_days, logs.RetentionDays.ONE_WEEK
            ),
            removal_policy=config.removal_policy,
        )

        self.macro_refresh_lambda = _lambda.DockerImageFunction(
            self,
            "MacroRefreshLambda",
            function_name=f"{prefix}-macro-refresh",
            architecture=_lambda.Architecture.ARM_64,
            code=_lambda.DockerImageCode.from_image_asset(
                str(project_root),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
                cmd=["src.handler.refresh_macro_series"],
            ),
            # Manheim pull does an LLM-extraction; FRED + AFDC together do a
            # handful of HTTP calls. 60s is plenty even with 3-4 retries.
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                "ENVIRONMENT": config.env_name,
                "MACRO_SERIES_TABLE_NAME": self.macro_series_table.table_name,
                "FRED_API_KEY_SECRET_ARN": self.fred_secret.secret_arn,
                "AFDC_API_KEY_SECRET_ARN": self.afdc_secret.secret_arn,
                # Manheim is a public press release — no key. Listed for parity.
            },
            tracing=_lambda.Tracing.DISABLED,
            description=f"Lookout macro-series refresh ({config.env_name})",
        )
        self.macro_refresh_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    macro_refresh_log_group.log_group_arn,
                    f"{macro_refresh_log_group.log_group_arn}:*",
                ],
            )
        )
        # Write to the macro-series cache table; that's the only DDB it touches.
        self.macro_refresh_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"],
                resources=[self.macro_series_table.table_arn],
            )
        )
        # Read FRED + AFDC keys.
        self.macro_refresh_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    self.fred_secret.secret_arn,
                    self.afdc_secret.secret_arn,
                ],
            )
        )
        # Bedrock invoke for the Manheim LLM extraction. Same model list as the
        # agent Lambda so we don't drift if the config changes.
        bedrock_model_ids_for_macro = sorted({config.llm_scoring_model, config.llm_drafting_model})
        self.macro_refresh_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/{model_id}"
                    for model_id in bedrock_model_ids_for_macro
                ],
            )
        )

        # Nightly at 06:00 UTC — FRED publishes weekday rates around 10am ET;
        # Manheim publishes its monthly index around the 7th, so a nightly
        # poll catches it within hours of release. AFDC moves slowly but
        # nightly keeps the cache fresh enough to surface expiration warnings.
        self.macro_refresh_rule = events.Rule(
            self,
            "MacroRefreshRule",
            rule_name=f"{prefix}-macro-refresh",
            schedule=events.Schedule.cron(minute="0", hour="6"),
            description=f"Lookout macro-series nightly refresh ({config.env_name})",
            enabled=True,
        )
        self.macro_refresh_rule.add_target(targets.LambdaFunction(self.macro_refresh_lambda))

        # Exports for downstream stacks
        self.lambda_function_name = self.agent_lambda.function.function_name
        self.lambda_function_arn = self.agent_lambda.function.function_arn
        self.runs_queue_url = self.runs_queue.queue_url
        self.runs_queue_arn = self.runs_queue.queue_arn
