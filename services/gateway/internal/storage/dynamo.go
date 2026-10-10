package storage

import (
	"context"
	"fmt"
	"strconv"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"

	"s3-dedup-engine/services/gateway/internal/constants"
)

// FileMeta holds prior image metadata stored under FILE# keys.
type FileMeta struct {
	S3Key     string
	Width     int
	Height    int
	SizeBytes int64
	ChunkHash string
	FullHash  string
}

// DynamoClient indexes file-name and chunk/full-hash records for visual dedup.
type DynamoClient interface {
	GetFileByName(ctx context.Context, fileName string) (FileMeta, bool, error)
	PutFileByName(ctx context.Context, fileName string, meta FileMeta) error
	DeleteFileByName(ctx context.Context, fileName string) error
	QueryChunkCandidates(ctx context.Context, chunkHash string) (bool, error)
	GetByChunkAndFull(ctx context.Context, chunkHash, fullHash string) (string, bool, error)
	PutChunkFull(ctx context.Context, chunkHash, fullHash, s3Key, fileName string) error
	DeleteChunkFull(ctx context.Context, chunkHash, fullHash string) error
}

// DynamoStore is a LocalStack-compatible DynamoClient implementation.
type DynamoStore struct {
	client *dynamodb.Client
	table  string
}

func NewDynamoStore(client *dynamodb.Client, table string) *DynamoStore {
	return &DynamoStore{client: client, table: table}
}

func (d *DynamoStore) GetFileByName(ctx context.Context, fileName string) (FileMeta, bool, error) {
	out, err := d.client.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(d.table),
		Key: map[string]types.AttributeValue{
			constants.DynamoPKAttr: &types.AttributeValueMemberS{Value: constants.PrefixFile + fileName},
			constants.DynamoSKAttr: &types.AttributeValueMemberS{Value: constants.DynamoSKMeta},
		},
	})
	if err != nil {
		return FileMeta{}, false, fmt.Errorf("dynamodb get file by name: %w", err)
	}
	if out.Item == nil {
		return FileMeta{}, false, nil
	}
	return fileMetaFromItem(out.Item), true, nil
}

func (d *DynamoStore) PutFileByName(ctx context.Context, fileName string, meta FileMeta) error {
	_, err := d.client.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(d.table),
		Item: map[string]types.AttributeValue{
			constants.DynamoPKAttr:        &types.AttributeValueMemberS{Value: constants.PrefixFile + fileName},
			constants.DynamoSKAttr:        &types.AttributeValueMemberS{Value: constants.DynamoSKMeta},
			constants.DynamoS3KeyAttr:     &types.AttributeValueMemberS{Value: meta.S3Key},
			constants.DynamoWidthAttr:     &types.AttributeValueMemberN{Value: strconv.Itoa(meta.Width)},
			constants.DynamoHeightAttr:    &types.AttributeValueMemberN{Value: strconv.Itoa(meta.Height)},
			constants.DynamoSizeBytesAttr: &types.AttributeValueMemberN{Value: strconv.FormatInt(meta.SizeBytes, 10)},
			constants.DynamoChunkHashAttr: &types.AttributeValueMemberS{Value: meta.ChunkHash},
			constants.DynamoFullHashAttr:  &types.AttributeValueMemberS{Value: meta.FullHash},
		},
	})
	if err != nil {
		return fmt.Errorf("dynamodb put file by name: %w", err)
	}
	return nil
}

func (d *DynamoStore) DeleteFileByName(ctx context.Context, fileName string) error {
	_, err := d.client.DeleteItem(ctx, &dynamodb.DeleteItemInput{
		TableName: aws.String(d.table),
		Key: map[string]types.AttributeValue{
			constants.DynamoPKAttr: &types.AttributeValueMemberS{Value: constants.PrefixFile + fileName},
			constants.DynamoSKAttr: &types.AttributeValueMemberS{Value: constants.DynamoSKMeta},
		},
	})
	if err != nil {
		return fmt.Errorf("dynamodb delete file by name: %w", err)
	}
	return nil
}

func (d *DynamoStore) QueryChunkCandidates(ctx context.Context, chunkHash string) (bool, error) {
	out, err := d.client.Query(ctx, &dynamodb.QueryInput{
		TableName:              aws.String(d.table),
		KeyConditionExpression: aws.String("#pk = :pk"),
		ExpressionAttributeNames: map[string]string{
			"#pk": constants.DynamoPKAttr,
		},
		ExpressionAttributeValues: map[string]types.AttributeValue{
			":pk": &types.AttributeValueMemberS{Value: constants.PrefixChunk + chunkHash},
		},
		Limit: aws.Int32(1),
	})
	if err != nil {
		return false, fmt.Errorf("dynamodb query chunk: %w", err)
	}
	return len(out.Items) > 0, nil
}

func (d *DynamoStore) GetByChunkAndFull(ctx context.Context, chunkHash, fullHash string) (string, bool, error) {
	out, err := d.client.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(d.table),
		Key: map[string]types.AttributeValue{
			constants.DynamoPKAttr: &types.AttributeValueMemberS{Value: constants.PrefixChunk + chunkHash},
			constants.DynamoSKAttr: &types.AttributeValueMemberS{Value: constants.PrefixFull + fullHash},
		},
	})
	if err != nil {
		return "", false, fmt.Errorf("dynamodb get chunk+full: %w", err)
	}
	if out.Item == nil {
		return "", false, nil
	}
	s3Key := ""
	if v, ok := out.Item[constants.DynamoS3KeyAttr].(*types.AttributeValueMemberS); ok {
		s3Key = v.Value
	}
	return s3Key, true, nil
}

func (d *DynamoStore) PutChunkFull(ctx context.Context, chunkHash, fullHash, s3Key, fileName string) error {
	_, err := d.client.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(d.table),
		Item: map[string]types.AttributeValue{
			constants.DynamoPKAttr:       &types.AttributeValueMemberS{Value: constants.PrefixChunk + chunkHash},
			constants.DynamoSKAttr:       &types.AttributeValueMemberS{Value: constants.PrefixFull + fullHash},
			constants.DynamoS3KeyAttr:    &types.AttributeValueMemberS{Value: s3Key},
			constants.DynamoFileNameAttr: &types.AttributeValueMemberS{Value: fileName},
		},
	})
	if err != nil {
		return fmt.Errorf("dynamodb put chunk+full: %w", err)
	}
	return nil
}

func (d *DynamoStore) DeleteChunkFull(ctx context.Context, chunkHash, fullHash string) error {
	_, err := d.client.DeleteItem(ctx, &dynamodb.DeleteItemInput{
		TableName: aws.String(d.table),
		Key: map[string]types.AttributeValue{
			constants.DynamoPKAttr: &types.AttributeValueMemberS{Value: constants.PrefixChunk + chunkHash},
			constants.DynamoSKAttr: &types.AttributeValueMemberS{Value: constants.PrefixFull + fullHash},
		},
	})
	if err != nil {
		return fmt.Errorf("dynamodb delete chunk+full: %w", err)
	}
	return nil
}

func fileMetaFromItem(item map[string]types.AttributeValue) FileMeta {
	meta := FileMeta{}
	if v, ok := item[constants.DynamoS3KeyAttr].(*types.AttributeValueMemberS); ok {
		meta.S3Key = v.Value
	}
	if v, ok := item[constants.DynamoWidthAttr].(*types.AttributeValueMemberN); ok {
		meta.Width, _ = strconv.Atoi(v.Value)
	}
	if v, ok := item[constants.DynamoHeightAttr].(*types.AttributeValueMemberN); ok {
		meta.Height, _ = strconv.Atoi(v.Value)
	}
	if v, ok := item[constants.DynamoSizeBytesAttr].(*types.AttributeValueMemberN); ok {
		meta.SizeBytes, _ = strconv.ParseInt(v.Value, 10, 64)
	}
	if v, ok := item[constants.DynamoChunkHashAttr].(*types.AttributeValueMemberS); ok {
		meta.ChunkHash = v.Value
	}
	if v, ok := item[constants.DynamoFullHashAttr].(*types.AttributeValueMemberS); ok {
		meta.FullHash = v.Value
	}
	return meta
}
