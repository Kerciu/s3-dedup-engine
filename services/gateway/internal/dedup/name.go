package dedup

import (
	"context"
	"fmt"
	"log/slog"

	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/storage"
)

type FileNameMatchPipeline struct {
	dynamo storage.DynamoClient
}

func NewFileNameMatchPipeline(dynamo storage.DynamoClient) *FileNameMatchPipeline {
	return &FileNameMatchPipeline{dynamo: dynamo}
}

func (p *FileNameMatchPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	meta, found, err := p.dynamo.GetFileByName(ctx, img.FileName)
	if err != nil {
		return 0, fmt.Errorf("file name lookup: %w", err)
	}
	if !found {
		slog.Debug("no prior file name record", "file", img.FileName)
		return DecisionContinue, nil
	}

	img.NameMatched = true
	img.ExistingS3Key = meta.S3Key
	slog.Info("file name collision",
		"file", img.FileName,
		"existing_s3_key", meta.S3Key,
		"prior_width", meta.Width,
		"prior_height", meta.Height,
		"prior_size_bytes", meta.SizeBytes,
	)
	return DecisionContinue, nil
}
