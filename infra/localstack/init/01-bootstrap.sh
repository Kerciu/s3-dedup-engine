#!/bin/sh
set -eu

AWS_REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET_NAME="${DEDUP_BUCKET:-raw-tickets-bucket}"
TABLE_NAME="${METADATA_TABLE:-ticket-hashes}"
ENDPOINT_URL="${LOCALSTACK_ENDPOINT:-http://localstack:4566}"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

echo "[Local] Creating S3 bucket: ${BUCKET_NAME}"
aws --endpoint-url="${ENDPOINT_URL}" s3api create-bucket \
  --bucket "${BUCKET_NAME}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}" \
  >/dev/null 2>&1 || true

echo "[Local] Creating DynamoDB table: ${TABLE_NAME}"
aws --endpoint-url="${ENDPOINT_URL}" dynamodb create-table \
  --table-name "${TABLE_NAME}" \
  --attribute-definitions \
    AttributeName=PK,AttributeType=S \
    AttributeName=SK,AttributeType=S \
  --key-schema \
    AttributeName=PK,KeyType=HASH \
    AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  >/dev/null 2>&1 || true

echo "[Local] LocalStack bootstrap finished"
