"""GitHub OIDC stack — federated identity for CI/CD.

Creates an OIDC provider and a single least-privilege deploy role that GitHub
Actions assumes via short-lived tokens. No long-lived AWS credentials in GitHub.

One instance per AWS account/environment: the dev-account role trusts the `dev`
branch, the prod-account role trusts ONLY the `production` GitHub Environment —
so the prod role is un-assumable from a PR, another branch, or any non-prod job.
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

# CDK bootstrap roles (default qualifier hnb659fds) the deploy role assumes to
# run `cdk deploy`. These hold the real CloudFormation/S3/ECR permissions; the
# GitHub role needs nothing more than sts:AssumeRole on these four.
_BOOTSTRAP_ROLES = ("deploy", "file-publishing", "image-publishing", "lookup")


class GitHubOidcStack(Stack):
    """IAM OIDC provider + a single least-privilege GitHub Actions deploy role.

    The role is assumable ONLY by jobs whose OIDC token subject EXACTLY matches
    `subject` (StringEquals — never a wildcard). That exact-match is what pins
    the prod role to the `production` environment.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        role_name: str,
        subject: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        oidc_provider = iam.OpenIdConnectProvider(
            self,
            "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        # Exact-match trust: aud AND sub are pinned with StringEquals. A wildcard
        # (StringLike `repo:org/repo:*`) would let any branch/PR/fork-tag assume
        # the role and defeat the environment isolation — never do that here.
        self.deploy_role = iam.Role(
            self,
            "GitHubDeployRole",
            role_name=role_name,
            assumed_by=iam.WebIdentityPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": subject,
                    },
                },
            ),
            description=f"GitHub Actions deploy role for {subject}",
        )

        # Only permission needed: assume this account's four CDK bootstrap roles.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/"
                    f"cdk-hnb659fds-{role}-role-{self.account}-{self.region}"
                    for role in _BOOTSTRAP_ROLES
                ],
            )
        )

        CfnOutput(
            self,
            "DeployRoleArn",
            value=self.deploy_role.role_arn,
            description=f"Deploy role ARN for {role_name} (set as a GitHub secret)",
        )
