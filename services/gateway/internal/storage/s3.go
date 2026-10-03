package storage

import (
	"bytes"
	"context"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"s3-dedup-engine/services/gateway/internal/constants"
)

// S3Client uploads heavy payloads and lightweight ticket metadata descriptors.
type S3Client interface {
	UploadPayload(ctx context.Context, key string, data []byte) error
	UploadMetadata(ctx context.Context, key string, meta []byte) error
}

// S3Store is a LocalStack-compatible S3Client implementation.
type S3Store struct {
	client *s3.Client
	bucket string
}

func NewS3Store(client *s3.Client, bucket string) *S3Store {
	return &S3Store{client: client, bucket: bucket}
}

func (s *S3Store) UploadPayload(ctx context.Context, key string, data []byte) error {
	return s.put(ctx, constants.S3PayloadKeyPrefix+key, data, "application/octet-stream")
}

func (s *S3Store) UploadMetadata(ctx context.Context, key string, meta []byte) error {
	return s.put(ctx, constants.S3MetadataKeyPrefix+key, meta, "application/json")
}

func (s *S3Store) put(ctx context.Context, key string, body []byte, contentType string) error {
	_, err := s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(s.bucket),
		Key:         aws.String(key),
		Body:        bytes.NewReader(body),
		ContentType: aws.String(contentType),
	})
	if err != nil {
		return fmt.Errorf("s3 put %s: %w", key, err)
	}
	return nil
}
