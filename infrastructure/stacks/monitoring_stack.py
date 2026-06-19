"""Monitoring stack — CloudWatch dashboards, alarms, and SNS topics.

Provides operational visibility into the agent's health and behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subs,
)

if TYPE_CHECKING:
    from constructs import Construct

    from infrastructure.config import EnvironmentConfig
    from infrastructure.stacks.agent_stack import AgentStack


class MonitoringStack(Stack):
    """CloudWatch dashboards, alarms, and SNS notification topics."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        agent_stack: AgentStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"lookout-{config.env_name}"
        function_name = agent_stack.lambda_function_name

        # --- SNS Topic for Alarms ---

        self.alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            topic_name=f"{prefix}-alarms",
            display_name=f"Lookout Alarms ({config.env_name})",
        )

        if config.alarm_email:
            self.alarm_topic.add_subscription(subs.EmailSubscription(config.alarm_email))

        # --- Lambda Metrics ---

        invocations_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Invocations",
            dimensions_map={"FunctionName": function_name},
            statistic="Sum",
            period=Duration.hours(1),
        )

        errors_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Errors",
            dimensions_map={"FunctionName": function_name},
            statistic="Sum",
            period=Duration.hours(1),
        )

        duration_p50_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Duration",
            dimensions_map={"FunctionName": function_name},
            statistic="p50",
            period=Duration.hours(1),
        )

        duration_p99_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Duration",
            dimensions_map={"FunctionName": function_name},
            statistic="p99",
            period=Duration.hours(1),
        )

        # --- CloudWatch Alarms ---

        # Error rate alarm: > 10%
        error_rate_alarm = cloudwatch.Alarm(
            self,
            "ErrorRateAlarm",
            alarm_name=f"{prefix}-error-rate",
            alarm_description=f"Lambda error rate > 10% ({config.env_name})",
            metric=cloudwatch.MathExpression(
                expression="errors / invocations * 100",
                using_metrics={
                    "errors": errors_metric,
                    "invocations": invocations_metric,
                },
                period=Duration.hours(1),
            ),
            threshold=10,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        error_rate_alarm.add_alarm_action(cw_actions.SnsAction(self.alarm_topic))

        # Duration alarm: > 80% of timeout
        duration_threshold_ms = config.lambda_timeout_seconds * 1000 * 0.8
        duration_alarm = cloudwatch.Alarm(
            self,
            "DurationAlarm",
            alarm_name=f"{prefix}-duration",
            alarm_description=(
                f"Lambda duration > 80% of timeout "
                f"({duration_threshold_ms / 1000:.0f}s of {config.lambda_timeout_seconds}s) "
                f"({config.env_name})"
            ),
            metric=duration_p99_metric,
            threshold=duration_threshold_ms,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        duration_alarm.add_alarm_action(cw_actions.SnsAction(self.alarm_topic))

        # --- CloudWatch Dashboard ---

        self.dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name=f"{prefix}-dashboard",
        )

        self.dashboard.add_widgets(
            # Row 1: Lambda health
            cloudwatch.GraphWidget(
                title="Lambda Invocations",
                left=[invocations_metric],
                width=8,
                height=6,
            ),
            cloudwatch.GraphWidget(
                title="Lambda Errors",
                left=[errors_metric],
                width=8,
                height=6,
            ),
            cloudwatch.SingleValueWidget(
                title="Error Rate Alarm",
                metrics=[errors_metric],
                width=8,
                height=6,
            ),
        )

        self.dashboard.add_widgets(
            # Row 2: Performance
            cloudwatch.GraphWidget(
                title="Duration (P50 / P99)",
                left=[duration_p50_metric, duration_p99_metric],
                width=12,
                height=6,
            ),
            cloudwatch.GraphWidget(
                title="DynamoDB Consumed Capacity",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedWriteCapacityUnits",
                        dimensions_map={"TableName": agent_stack.runs_table.table_name},
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="AWS/DynamoDB",
                        metric_name="ConsumedWriteCapacityUnits",
                        dimensions_map={"TableName": agent_stack.deals_table.table_name},
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                ],
                width=12,
                height=6,
            ),
        )
