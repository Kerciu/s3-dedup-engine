package storage

import (
	"bytes"
	"context"
	"fmt"
	"io"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"s3-dedup-engine/services/gateway/internal/constants"
)

// S3Client uploads image payloads and lightweight metadata descriptors.
type S3Client interface {
	UploadPayload(ctx context.Context, key string, body io.Reader) error
	UploadMetadata(ctx context.Context, key string, meta []byte) error
	DeleteObject(ctx context.Context, key string) error
	DeleteMetadata(ctx context.Context, key string) error
	PayloadBytes(ctx context.Context, key string) (int64, error)
}

// S3Store is a LocalStack-compatible S3Client implementation.
type S3Store struct {
	client *s3.Client
	bucket string
}

func NewS3Store(client *s3.Client, bucket string) *S3Store {
	return &S3Store{client: client, bucket: bucket}
}

func (s *S3Store) UploadPayload(ctx context.Context, key string, body io.Reader) error {
	return s.put(ctx, constants.S3PayloadKeyPrefix+key, body, "application/octet-stream")
}

func (s *S3Store) UploadMetadata(ctx context.Context, key string, meta []byte) error {
	return s.put(ctx, constants.S3MetadataKeyPrefix+key, bytes.NewReader(meta), "application/json")
}

func (s *S3Store) DeleteObject(ctx context.Context, key string) error {
	return s.delete(ctx, constants.S3PayloadKeyPrefix+key)
}

func (s *S3Store) DeleteMetadata(ctx context.Context, key string) error {
	return s.delete(ctx, constants.S3MetadataKeyPrefix+key)
}

func (s *S3Store) delete(ctx context.Context, key string) error {
	_, err := s.client.DeleteObject(ctx, &s3.DeleteObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return fmt.Errorf("s3 delete %s: %w", key, err)
	}
	return nil
}

func (s *S3Store) PayloadBytes(ctx context.Context, key string) (int64, error) {
	fullKey := constants.S3PayloadKeyPrefix + key
	out, err := s.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(fullKey),
	})
	if err != nil {
		return 0, fmt.Errorf("s3 head %s: %w", fullKey, err)
	}
	if out.ContentLength == nil {
		return 0, fmt.Errorf("s3 head %s: missing content length", fullKey)
	}
	return *out.ContentLength, nil
}

func (s *S3Store) put(ctx context.Context, key string, body io.Reader, contentType string) error {
	_, err := s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(s.bucket),
		Key:         aws.String(key),
		Body:        body,
		ContentType: aws.String(contentType),
	})
	if err != nil {
		return fmt.Errorf("s3 put %s: %w", key, err)
	}
	return nil
}
