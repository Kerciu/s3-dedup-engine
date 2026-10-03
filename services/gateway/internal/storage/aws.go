package storage

import (
	"context"
	"fmt"
	"os"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"s3-dedup-engine/services/gateway/internal/constants"
)

type Clients struct {
	S3     S3Client
	Dynamo DynamoClient
}

func NewClients(ctx context.Context) (*Clients, error) {
	endpoint := envOr(constants.EnvS3EndpointURL, constants.DefaultLocalStackEndpoint)
	region := envOr(constants.EnvAWSRegion, constants.DefaultAWSRegion)
	accessKey := envOr(constants.EnvAWSAccessKeyID, constants.DefaultAWSAccessKeyID)
	secretKey := envOr(constants.EnvAWSSecretAccessKey, constants.DefaultAWSSecretAccessKey)
	bucket := envOr(constants.EnvDedupBucket, constants.DefaultS3Bucket)
	table := envOr(constants.EnvMetadataTable, constants.DefaultDynamoTable)

	cfg, err := config.LoadDefaultConfig(ctx,
		config.WithRegion(region),
		config.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")),
	)
	if err != nil {
		return nil, fmt.Errorf("load aws config: %w", err)
	}

	s3Client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		o.BaseEndpoint = aws.String(endpoint)
		o.UsePathStyle = true
	})

	dynamoEndpoint := envOr(constants.EnvDynamoEndpointURL, endpoint)
	dynamoClient := dynamodb.NewFromConfig(cfg, func(o *dynamodb.Options) {
		o.BaseEndpoint = aws.String(dynamoEndpoint)
	})

	return &Clients{
		S3:     NewS3Store(s3Client, bucket),
		Dynamo: NewDynamoStore(dynamoClient, table),
	}, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
