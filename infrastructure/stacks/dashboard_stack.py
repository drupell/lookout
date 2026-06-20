"""Dashboard hosting stack — S3 + CloudFront for the Next.js static export.

Provisions infrastructure only. Content sync is decoupled (see `make
frontend-deploy-{dev,prod}`) so:
  - `cdk synth` works before the frontend has ever been built.
  - Dashboard redeploys don't require a CloudFormation update — just an
    `aws s3 sync` + a CloudFront invalidation.
  - Diffs in CDK output stay focused on infra changes.

Bucket is private (no public access; OAC-only). Trailing-slash URI rewrite
happens in a CloudFront Function so static-exported pages like `/sign-in/`
resolve to the right `index.html` inside the bucket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_s3 as s3,
)

if TYPE_CHECKING:
    from constructs import Construct

    from infrastructure.config import EnvironmentConfig


# CloudFront Function: rewrite incoming URIs so static-exported routes resolve.
#   /                 → /index.html         (handled by default_root_object)
#   /sign-in/         → /sign-in/index.html
#   /sign-in          → /sign-in/index.html
#   /_next/static/... → unchanged (has a dot)
# Cheaper than Lambda@Edge and runs at every edge location.
_URI_REWRITE_FUNCTION = """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith('/')) {
    request.uri += 'index.html';
  } else if (!uri.includes('.')) {
    request.uri += '/index.html';
  }
  return request;
}
"""


class DashboardStack(Stack):
    """Private S3 bucket + CloudFront distribution for the Next.js dashboard."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"lookout-{config.env_name}"
        is_destroy = config.removal_policy == RemovalPolicy.DESTROY

        # --- S3 bucket (private, OAC-only) ---

        self.bucket = s3.Bucket(
            self,
            "DashboardBucket",
            bucket_name=f"{prefix}-dashboard",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=config.removal_policy,
            # Only auto-empty in dev. Prod RETAIN means the bucket survives stack deletion.
            auto_delete_objects=is_destroy,
        )

        # --- CloudFront URI-rewrite function ---

        rewrite_function = cloudfront.Function(
            self,
            "UriRewrite",
            code=cloudfront.FunctionCode.from_inline(_URI_REWRITE_FUNCTION),
            comment="Rewrite trailing-slash routes to /index.html for static export",
        )

        # --- CloudFront distribution ---

        self.distribution = cloudfront.Distribution(
            self,
            "DashboardDistribution",
            comment=f"Lookout {config.env_name} dashboard",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=rewrite_function,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    ),
                ],
            ),
            # Both 403 (S3 returns this for missing keys behind OAC) and 404
            # render the Next-exported 404 page so deep links never 500.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/404.html",
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=404,
                    response_page_path="/404.html",
                ),
            ],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            # NA + EU only — covers the target audience and keeps CloudFront ~30% cheaper
            # than the all-edges price class. Bump to PRICE_CLASS_ALL when expanding.
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            enable_logging=False,
        )

        # --- Outputs (consumed by `make frontend-deploy-*`) ---

        CfnOutput(self, "DashboardBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "DashboardDistributionId", value=self.distribution.distribution_id)
        CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://{self.distribution.distribution_domain_name}",
        )
