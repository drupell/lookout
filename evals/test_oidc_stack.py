"""Structural eval: the GitHub OIDC deploy role uses exact-match, env-scoped trust.

Guards the security-critical invariant — the prod deploy role must be assumable
ONLY from a job running in the `production` GitHub Environment, never from a PR
or another branch. A wildcard `StringLike repo:org/repo:*` would silently break
that isolation, so we assert it never appears.

No AWS calls; pure CDK synth + template assertions.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infrastructure.stacks.github_oidc_stack import GitHubOidcStack


def _template(construct_id: str, role_name: str, subject: str) -> Template:
    app = cdk.App()
    stack = GitHubOidcStack(
        app,
        construct_id,
        role_name=role_name,
        subject=subject,
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    return Template.from_stack(stack)


def test_prod_role_trust_is_stringequals_on_environment_sub() -> None:
    """Prod role: sub pinned with StringEquals to the production environment."""
    t = _template(
        "LookoutProdOidc",
        "lookout-gha-prod",
        "repo:drupell/lookout:environment:production",
    )
    t.has_resource_properties(
        "AWS::IAM::Role",
        Match.object_like(
            {
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        Match.object_like(
                            {
                                "Condition": {
                                    "StringEquals": {
                                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                                        "token.actions.githubusercontent.com:sub": "repo:drupell/lookout:environment:production",
                                    }
                                }
                            }
                        )
                    ]
                }
            }
        ),
    )


def test_no_wildcard_stringlike_on_sub() -> None:
    """No StringLike wildcard anywhere — that would defeat the isolation."""
    t = _template(
        "LookoutDevOidc",
        "lookout-gha-dev",
        "repo:drupell/lookout:ref:refs/heads/dev",
    )
    body = str(t.to_json())
    assert "StringLike" not in body, "OIDC trust must use StringEquals, not a wildcard StringLike"
    assert "repo:drupell/lookout:*" not in body, "wildcard subject must not appear"


def test_deploy_role_only_assumes_the_four_cdk_bootstrap_roles() -> None:
    """Least privilege: sts:AssumeRole on the 4 cdk roles only — no extras."""
    t = _template(
        "LookoutDevOidc",
        "lookout-gha-dev",
        "repo:drupell/lookout:ref:refs/heads/dev",
    )
    body = str(t.to_json())
    for role in ("deploy", "file-publishing", "image-publishing", "lookup"):
        assert f"cdk-hnb659fds-{role}-role" in body, f"missing AssumeRole on cdk {role} role"
    assert "cdk-hnb659fds-*" not in body, "no wildcard on the bootstrap role ARNs"
    assert "lambda:InvokeFunction" not in body, "deploy role should hold no extra permissions"
