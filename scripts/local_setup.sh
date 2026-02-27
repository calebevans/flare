#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

AWS="aws --endpoint-url $ENDPOINT --region $REGION"

echo "==> Creating SNS topic..."
$AWS sns create-topic --name "flare-local" --output text --query TopicArn

echo "==> Creating DynamoDB incidents table..."
$AWS dynamodb create-table \
    --table-name "flare-incidents-local" \
    --key-schema AttributeName=incident_id,KeyType=HASH \
    --attribute-definitions AttributeName=incident_id,AttributeType=S \
    --billing-mode PAY_PER_REQUEST \
    --output text --query TableDescription.TableName 2>/dev/null || echo "    (table already exists)"

echo "==> Seeding log groups..."
python "$SCRIPT_DIR/seed_logs.py" "/test/app" "$REPO_DIR/tests/fixtures/sample_logs.txt"
python "$SCRIPT_DIR/seed_logs.py" "/aws/apache/server" "$REPO_DIR/tests/fixtures/apache_sample.log"

echo ""
echo "==> Done. LocalStack is seeded and ready."
echo "    Log groups: /test/app (65 lines), /aws/apache/server (2004 lines)"
echo "    SNS topic:  arn:aws:sns:$REGION:000000000000:flare-local"
echo "    DynamoDB:   flare-incidents-local"
