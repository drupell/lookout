#!/usr/bin/env bash
# Pause Lookout — disables EventBridge rule and sets Lambda concurrency to 0.
set -euo pipefail

ENV="${1:-dev}"
RULE_NAME="lookout-${ENV}-schedule"
FUNCTION_NAME="lookout-${ENV}"

echo "Pausing Lookout (${ENV})..."

echo "  Disabling EventBridge rule: ${RULE_NAME}"
aws events disable-rule --name "${RULE_NAME}"

echo "  Setting Lambda concurrency to 0: ${FUNCTION_NAME}"
aws lambda put-function-concurrency \
    --function-name "${FUNCTION_NAME}" \
    --reserved-concurrent-executions 0

echo "Done. Agent is paused."
