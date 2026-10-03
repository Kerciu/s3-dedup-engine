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
	DownloadPayload(ctx context.Context, key string) ([]byte, error)
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

func (s *S3Store) DownloadPayload(ctx context.Context, key string) ([]byte, error) {
	out, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("s3 get %s: %w", key, err)
	}
	defer out.Body.Close()
	data, err := io.ReadAll(out.Body)
	if err != nil {
		return nil, fmt.Errorf("s3 read %s: %w", key, err)
	}
	return data, nil
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
