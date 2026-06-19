"""Reusable CDK construct for the agent Lambda function with Docker packaging.

Docker packaging is required because the Lambda needs Playwright and FAISS,
which have native dependencies that don't work with standard zip packaging.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aws_cdk import Duration
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct

if TYPE_CHECKING:
    from infrastructure.config import EnvironmentConfig

_RETENTION_DAYS_MAP = {
    1: logs.RetentionDays.ONE_DAY,
    3: logs.RetentionDays.THREE_DAYS,
    5: logs.RetentionDays.FIVE_DAYS,
    7: logs.RetentionDays.ONE_WEEK,
    14: logs.RetentionDays.TWO_WEEKS,
    30: logs.RetentionDays.ONE_MONTH,
    60: logs.RetentionDays.TWO_MONTHS,
    90: logs.RetentionDays.THREE_MONTHS,
    365: logs.RetentionDays.ONE_YEAR,
}


class AgentLambdaFunction(Construct):
    """Lambda function construct with Docker image packaging."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
    ) -> None:
        super().__init__(scope, construct_id)

        project_root = Path(__file__).parent.parent.parent

        self.function = _lambda.DockerImageFunction(
            self,
            "AgentFunction",
            function_name=f"lookout-{config.env_name}",
            architecture=_lambda.Architecture.ARM_64,
            code=_lambda.DockerImageCode.from_image_asset(
                str(project_root),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
            ),
            timeout=Duration.seconds(config.lambda_timeout_seconds),
            memory_size=config.lambda_memory_mb,
            environment={
                "TEST_MODE": str(config.test_mode).lower(),
                "ENVIRONMENT": config.env_name,
                "DEAL_SCORE_THRESHOLD": str(config.deal_score_threshold),
                "LLM_PROVIDER": config.llm_provider,
                "LLM_SCORING_MODEL": config.llm_scoring_model,
                "LLM_DRAFTING_MODEL": config.llm_drafting_model,
                "SKIP_DRAFTING": str(config.skip_drafting).lower(),
                "REFRESH_DEALS_ON_RUN": str(config.refresh_deals_on_run).lower(),
            },
            # X-Ray tracing is NOT set here to avoid CDK's auto-generated
            # Resource=* IAM policy. Instead, we set the tracing config and
            # IAM permissions explicitly in agent_stack.py with scoped ARNs.
            tracing=_lambda.Tracing.DISABLED,
            description=f"Lookout agent ({config.env_name})",
        )

        retention = _RETENTION_DAYS_MAP.get(config.log_retention_days, logs.RetentionDays.ONE_WEEK)

        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/aws/lambda/lookout-{config.env_name}",
            retention=retention,
            removal_policy=config.removal_policy,
        )
