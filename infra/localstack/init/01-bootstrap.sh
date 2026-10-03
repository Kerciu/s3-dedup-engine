#!/bin/sh
set -eu

AWS_REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET_NAME="${DEDUP_BUCKET:-raw-tickets-bucket}"
TABLE_NAME="${METADATA_TABLE:-ticket-hashes}"

echo "[Local] Creating S3 bucket: ${BUCKET_NAME}"
awslocal s3api create-bucket \
  --bucket "${BUCKET_NAME}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}" \
  >/dev/null 2>&1 || true

echo "[Local] Creating DynamoDB table: ${TABLE_NAME}"
awslocal dynamodb create-table \
  --table-name "${TABLE_NAME}" \
  --attribute-definitions \
    AttributeName=FileHash,AttributeType=S \
  --key-schema \
    AttributeName=FileHash,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  >/dev/null 2>&1 || true

echo "[Local] LocalStack bootstrap finished"
