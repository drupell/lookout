#!/usr/bin/env bash
# Resume Lookout — re-enables EventBridge rule and removes Lambda concurrency limit.
set -euo pipefail

ENV="${1:-dev}"
RULE_NAME="lookout-${ENV}-schedule"
FUNCTION_NAME="lookout-${ENV}"

echo "Resuming Lookout (${ENV})..."

echo "  Enabling EventBridge rule: ${RULE_NAME}"
aws events enable-rule --name "${RULE_NAME}"

echo "  Removing Lambda concurrency limit: ${FUNCTION_NAME}"
aws lambda delete-function-concurrency \
    --function-name "${FUNCTION_NAME}"

echo "Done. Agent is active."
