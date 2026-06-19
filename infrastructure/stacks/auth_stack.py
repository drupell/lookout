"""Auth stack — Cognito User Pool + Users DynamoDB table + Hosted UI domain.

Provides identity for the multi-tenant dashboard. The Users table holds
per-user metadata (tier, optional BYOK secret ARN, custom prefs overrides).

Auth uses Cognito Hosted UI (OAuth Authorization Code + PKCE):
  - Dashboard redirects to https://<cognito-domain>/login?...
  - User authenticates on Cognito's hosted page
  - Cognito redirects back to /auth/callback with `?code=...`
  - Frontend exchanges the code for ID + access tokens via the OIDC client

Sign-up is admin-only by default — create users via the AWS console or CLI
during dev. Switch to self-signup before opening to public.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)

if TYPE_CHECKING:
    from constructs import Construct

    from infrastructure.config import EnvironmentConfig


class AuthStack(Stack):
    """Cognito User Pool + Hosted UI domain + OAuth client + Users metadata table."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        dashboard_domain_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"lookout-{config.env_name}"
        is_dev = config.env_name == "dev"

        # --- Cognito User Pool ---

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{prefix}-users",
            self_sign_up_enabled=False,  # admin-creates users during dev
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=False),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            # ESSENTIALS unlocks Managed Login (the modern Hosted UI). Per-MAU
            # rate is higher than LITE ($0.015 vs $0.005) but the free tier
            # covers our scale. Stays well under PLUS, which adds compromised-
            # credential / risk scoring we don't need.
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            removal_policy=config.removal_policy,
        )

        # --- Hosted UI domain (Managed Login) ---
        # Cognito-prefix domain (free, AWS-hosted). Format:
        #   https://<prefix>.auth.us-east-1.amazoncognito.com
        # Prefix must be globally unique across all Cognito tenants in the region.
        # Override via LOOKOUT_{ENV}_COGNITO_DOMAIN_PREFIX env var if the default
        # collides; otherwise CFN will fail with "Domain already associated."
        # NEWER_MANAGED_LOGIN serves AWS's modernized hosted UI (2024 redesign)
        # rather than the legacy classic flow.
        self.user_pool_domain = self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=config.cognito_domain_prefix,
            ),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )

        # --- App client (public SPA, OAuth Authorization Code + PKCE) ---

        dashboard_url = f"https://{dashboard_domain_name}"
        callback_urls = [f"{dashboard_url}/auth/callback/"]
        logout_urls = [f"{dashboard_url}/"]
        # Local dev convenience: include localhost so `npm run dev` can complete
        # the OAuth round-trip without a separate client.
        if is_dev:
            callback_urls.append("http://localhost:3000/auth/callback/")
            logout_urls.append("http://localhost:3000/")

        self.user_pool_client = self.user_pool.add_client(
            "DashboardClient",
            user_pool_client_name=f"{prefix}-dashboard",
            generate_secret=False,  # public SPA client — secret would leak in browser
            auth_flows=cognito.AuthFlow(
                user_srp=False,
                user_password=False,
                admin_user_password=False,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=callback_urls,
                logout_urls=logout_urls,
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            prevent_user_existence_errors=True,
        )

        # --- Managed Login branding ---
        # `use_cognito_provided_values=True` opts into AWS's curated default
        # theme — already much nicer than classic Hosted UI without writing a
        # custom Settings JSON. Swap for a custom `settings`/`assets` payload
        # when we want full brand control (logo, colors, button styling).
        cognito.CfnManagedLoginBranding(
            self,
            "DashboardLoginBranding",
            user_pool_id=self.user_pool.user_pool_id,
            client_id=self.user_pool_client.user_pool_client_id,
            use_cognito_provided_values=True,
        )

        # --- Users metadata table ---
        # Schema:
        #   user_id (PK)        — Cognito sub (uuid)
        #   email
        #   tier                — "default" | "byok"
        #   marketcheck_secret_arn  (only when tier=byok)
        #   prefs_overrides     — JSON (subset of preferences.yaml shape)
        #   created_at, updated_at

        self.users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name=f"{prefix}-users",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )
        # GSI for email lookup (admin tooling)
        self.users_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
        )

        # --- Favorites table (generic per-user feedback store) ---
        # Schema:
        #   user_id (PK), listing_id (SK)
        #   signal      — "FAVORITE" today; DISLIKE later (dislike-ready)
        #   created_at
        #   deal        — full deal snapshot at favorite-time, so a favorite
        #                  survives the deal row's 90-day TTL and run rotation
        #   note        — optional user text
        # Identity-scoped like the Users table and written only by the API
        # Lambda. Deliberately NO TTL — favorites are curated user data.
        self.favorites_table = dynamodb.Table(
            self,
            "FavoritesTable",
            table_name=f"{prefix}-favorites",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="listing_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=config.removal_policy,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # --- Outputs (for the dashboard build + ApiStack) ---

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(
            self,
            "CognitoDomain",
            value=f"{config.cognito_domain_prefix}.auth.{self.region}.amazoncognito.com",
        )
        CfnOutput(self, "UsersTableName", value=self.users_table.table_name)
        CfnOutput(self, "FavoritesTableName", value=self.favorites_table.table_name)
