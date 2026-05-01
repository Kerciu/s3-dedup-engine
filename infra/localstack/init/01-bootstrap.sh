#!/bin/sh
set -eu

AWS_REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET_NAME="${DEDUP_BUCKET:-dedup-canonical-files}"
TABLE_NAME="${METADATA_TABLE:-dedup-metadata}"

echo "Creating S3 bucket: ${BUCKET_NAME}"
awslocal s3api create-bucket \
  --bucket "${BUCKET_NAME}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}" \
  >/dev/null 2>&1 || true

echo "Creating DynamoDB table: ${TABLE_NAME}"
awslocal dynamodb create-table \
  --table-name "${TABLE_NAME}" \
  --attribute-definitions \
    AttributeName=pk,AttributeType=S \
    AttributeName=sk,AttributeType=S \
  --key-schema \
    AttributeName=pk,KeyType=HASH \
    AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  >/dev/null 2>&1 || true

echo "LocalStack bootstrap finished"
