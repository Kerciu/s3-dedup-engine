package storage

import (
	"context"
	"errors"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"

	"s3-dedup-engine/services/gateway/internal/constants"
)

// DynamoClient looks up exact content hashes for collision detection.
type DynamoClient interface {
	CheckHashExists(ctx context.Context, hash string) (bool, error)
}

// DynamoStore is a LocalStack-compatible DynamoClient implementation.
type DynamoStore struct {
	client *dynamodb.Client
	table  string
}

func NewDynamoStore(client *dynamodb.Client, table string) *DynamoStore {
	return &DynamoStore{client: client, table: table}
}

func (d *DynamoStore) CheckHashExists(ctx context.Context, hash string) (bool, error) {
	out, err := d.client.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(d.table),
		Key: map[string]types.AttributeValue{
			constants.DynamoFileHashAttr: &types.AttributeValueMemberS{Value: hash},
		},
	})
	if err != nil {
		var rnfe *types.ResourceNotFoundException
		if errors.As(err, &rnfe) {
			return false, nil
		}
		return false, fmt.Errorf("dynamodb get item: %w", err)
	}
	return out.Item != nil, nil
}
