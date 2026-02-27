#!/usr/bin/env bash
set -euo pipefail

DEMO_FUNCTION="${1:-flare-demo-failing}"
FLARE_FUNCTION="${2:-}"
INVOCATIONS="${3:-5}"
MODE="${4:-mixed}"

echo "=== Invoking demo function '$DEMO_FUNCTION' ${INVOCATIONS}x with mode=$MODE ==="
for i in $(seq 1 "$INVOCATIONS"); do
    echo "  Invocation $i/$INVOCATIONS..."
    aws lambda invoke \
        --function-name "$DEMO_FUNCTION" \
        --payload "{\"mode\": \"$MODE\"}" \
        --cli-binary-format raw-in-base64-out \
        /dev/null 2>/dev/null || true
    sleep 1
done

echo ""
echo "=== Waiting 10s for CloudWatch Logs to propagate ==="
sleep 10

if [ -n "$FLARE_FUNCTION" ]; then
    echo "=== Invoking Flare function '$FLARE_FUNCTION' ==="
    aws lambda invoke \
        --function-name "$FLARE_FUNCTION" \
        --payload '{}' \
        --cli-binary-format raw-in-base64-out \
        /tmp/flare-output.json
    echo ""
    echo "=== Flare response ==="
    cat /tmp/flare-output.json
    echo ""

    # Check if voice pipeline stored an incident
    TABLE_NAME="${5:-}"
    if [ -n "$TABLE_NAME" ]; then
        echo ""
        echo "=== Checking DynamoDB for incident record ==="
        aws dynamodb scan \
            --table-name "$TABLE_NAME" \
            --max-items 1 \
            --query 'Items[0].{id:incident_id.S,status:prefetch_status.S,alarm:alarm_name.S}' \
            --output table 2>/dev/null || echo "    (no incidents table or no records)"
    fi
else
    echo "No Flare function specified. Pass it as the second argument to trigger analysis."
    echo "Usage: $0 <demo-function> <flare-function> [invocations] [mode] [incidents-table]"
fi
