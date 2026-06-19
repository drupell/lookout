"""GitHub OIDC stack — federated identity for CI/CD.

Creates an OIDC provider and IAM role that GitHub Actions assumes
via short-lived tokens. No long-lived AWS credentials stored in GitHub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_cdk import (
    CfnOutput,
    Stack,
)
from aws_cdk import (
    aws_iam as iam,
)

if TYPE_CHECKING:
    from constructs import Construct


class GitHubOidcStack(Stack):
    """IAM OIDC provider and deploy role for GitHub Actions."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        github_org: str,
        github_repo: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # OIDC provider for GitHub Actions
        oidc_provider = iam.OpenIdConnectProvider(
            self,
            "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        # Deploy role — scoped to this specific repo
        self.deploy_role = iam.Role(
            self,
            "GitHubDeployRole",
            role_name="lookout-github-deploy",
            assumed_by=iam.WebIdentityPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": (
                            f"repo:{github_org}/{github_repo}:*"
                        ),
                    },
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                },
            ),
            description="GitHub Actions deploy role for Lookout",
        )

        # CDK deploy permissions
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*",
                ],
            )
        )

        # Lambda invoke for integration tests
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:us-east-1:{self.account}:function:lookout-*",
                ],
            )
        )

        CfnOutput(
            self,
            "DeployRoleArn",
            value=self.deploy_role.role_arn,
            description="Add this as AWS_DEPLOY_ROLE_ARN secret in GitHub repo settings",
        )
