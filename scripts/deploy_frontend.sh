#!/usr/bin/env bash
# Build the Next.js dashboard with stack-derived env vars, sync to S3, and
# invalidate CloudFront. Run after the corresponding CDK stacks are deployed.
#
# Usage: scripts/deploy_frontend.sh <env>
#   env = "dev" or "prod"
set -euo pipefail

ENV="${1:?usage: $0 <dev|prod>}"
REGION="us-east-1"

case "$ENV" in
  dev)
    AUTH_STACK="LookoutDevAuth"
    API_STACK="LookoutDevApi"
    DASH_STACK="LookoutDevDashboard"
    PROFILE="${AWS_PROFILE:-lookout-dev-admin}"
    ;;
  prod)
    AUTH_STACK="LookoutProdAuth"
    API_STACK="LookoutProdApi"
    DASH_STACK="LookoutProdDashboard"
    PROFILE="${AWS_PROFILE:-lookout-prod-admin}"
    ;;
  *)
    echo "Unknown env: $ENV (expected dev or prod)" >&2
    exit 1
    ;;
esac

get_output() {
  local stack="$1"
  local key="$2"
  aws cloudformation describe-stacks \
    --profile "$PROFILE" --region "$REGION" \
    --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" \
    --output text
}

echo "==> Reading stack outputs..."
USER_POOL_ID=$(get_output "$AUTH_STACK" UserPoolId)
USER_POOL_CLIENT_ID=$(get_output "$AUTH_STACK" UserPoolClientId)
COGNITO_DOMAIN=$(get_output "$AUTH_STACK" CognitoDomain)
API_URL=$(get_output "$API_STACK" ApiUrl)
BUCKET=$(get_output "$DASH_STACK" DashboardBucketName)
DIST_ID=$(get_output "$DASH_STACK" DashboardDistributionId)
DASH_URL=$(get_output "$DASH_STACK" DashboardUrl)

for var in USER_POOL_ID USER_POOL_CLIENT_ID COGNITO_DOMAIN API_URL BUCKET DIST_ID; do
  if [[ -z "${!var}" || "${!var}" == "None" ]]; then
    echo "ERROR: Output $var is empty — has the stack been deployed?" >&2
    exit 1
  fi
done

echo "==> Building frontend with stack outputs as env..."
(
  cd frontend
  NEXT_PUBLIC_COGNITO_USER_POOL_ID="$USER_POOL_ID" \
  NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID="$USER_POOL_CLIENT_ID" \
  NEXT_PUBLIC_COGNITO_REGION="$REGION" \
  NEXT_PUBLIC_COGNITO_DOMAIN="$COGNITO_DOMAIN" \
  NEXT_PUBLIC_API_BASE_URL="${API_URL%/}" \
    npm run build
)

echo "==> Syncing s3://$BUCKET ..."
aws s3 sync frontend/out/ "s3://$BUCKET/" \
  --profile "$PROFILE" --region "$REGION" --delete

echo "==> Invalidating CloudFront distribution $DIST_ID ..."
aws cloudfront create-invalidation \
  --profile "$PROFILE" \
  --distribution-id "$DIST_ID" \
  --paths "/*" \
  --output text > /dev/null

echo
echo "==> Done."
echo "Dashboard: $DASH_URL"
