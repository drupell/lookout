# ADR 004: OIDC Federation Over Static AWS Credentials

## Status

Accepted

## Context

The CI/CD pipeline (GitHub Actions) must authenticate to AWS to deploy CDK stacks, invoke Lambda functions, and run integration tests. Two authentication approaches are available:

1. **Static credentials:** Store `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` as GitHub repository secrets.
2. **OIDC federation:** Configure an IAM Identity Provider trust relationship with GitHub's OIDC provider, allowing GitHub Actions to assume an IAM role with short-lived session credentials.

This decision is evaluated through the lens of financial services credential management standards, where long-lived credentials are a finding in security audits.

## Decision

Use OIDC federation exclusively. No static AWS credentials are stored in GitHub at any level (repository, organization, or environment secrets). The pipeline uses `aws-actions/configure-aws-credentials@v4` with `role-to-assume` only.

## Consequences

### Positive

- **No long-lived secrets.** OIDC tokens are short-lived (typically 1 hour) and scoped to the specific workflow run. There are no persistent credentials that can be exfiltrated from GitHub's secrets store.
- **Audit trail.** Every credential assumption is logged in AWS CloudTrail with the GitHub repository, workflow, branch, and commit SHA as session tags. This provides full traceability from AWS API call back to the specific CI run.
- **Rotation-free.** Static credentials require periodic rotation (typically 90 days in regulated environments). OIDC credentials are ephemeral and never need rotation.
- **Blast radius control.** The IAM role's trust policy can restrict assumption to specific repositories, branches, and even workflow names. A compromised fork cannot assume the production deployment role.
- **Compliance alignment.** Financial services regulators (OCC, FFIEC) and frameworks (SOC 2, ISO 27001) require that service accounts use short-lived credentials where technically feasible. OIDC satisfies this requirement without exception requests.

### Negative

- **Initial setup complexity.** Configuring the OIDC Identity Provider in IAM and writing the trust policy requires more upfront work than storing two secrets.
- **AWS account dependency.** The OIDC trust relationship is configured per AWS account. Multi-account deployments require trust policies in each target account.
- **Debugging opacity.** When OIDC assumption fails, the error messages from AWS STS can be cryptic. Trust policy mismatches (wrong subject claim, wrong audience) require careful debugging.

## Alternatives Considered

- **Static IAM user credentials:** Rejected. Long-lived credentials stored in a third-party system (GitHub) violate the principle of least privilege in time. Even with rotation, there is a window of exposure. In a financial services context, this would be flagged in any credential management audit.
- **GitHub Apps authentication:** Considered for GitHub API access (PR comments, status checks) but not applicable for AWS authentication.
- **AWS SSO / IAM Identity Center:** Appropriate for human users but not for CI/CD machine identity. OIDC federation is the standard pattern for workload identity from external CI systems.
