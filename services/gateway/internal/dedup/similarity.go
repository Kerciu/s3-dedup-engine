package dedup

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
	"s3-dedup-engine/services/gateway/internal/storage"
)

type SimilarityPipeline struct {
	client *grpcclient.SimilarityClient
	s3     storage.S3Client
}

func NewSimilarityPipeline(client *grpcclient.SimilarityClient, s3 storage.S3Client) *SimilarityPipeline {
	return &SimilarityPipeline{client: client, s3: s3}
}

func (p *SimilarityPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	imageBytes, err := os.ReadFile(img.FilePath)
	if err != nil {
		return 0, fmt.Errorf("read image for similarity: %w", err)
	}
	slog.Debug("loaded image bytes for similarity", "file", img.FileName, "bytes", len(imageBytes))

	var reference []byte
	if img.ExistingS3Key != "" {
		reference, err = p.s3.DownloadPayload(ctx, img.ExistingS3Key)
		if err != nil {
			slog.Warn("failed to load reference image",
				"s3_key", img.ExistingS3Key,
				"error", err,
			)
			reference = nil
		} else {
			slog.Debug("loaded reference image",
				"s3_key", img.ExistingS3Key,
				"bytes", len(reference),
			)
		}
	} else {
		slog.Debug("no reference image available for similarity", "file", img.FileName)
	}

	score, err := p.client.CheckSimilarity(ctx, imageBytes, reference, img.FileName)
	if err != nil {
		return 0, fmt.Errorf("check similarity: %w", err)
	}
	img.SimilarityScore = score
	slog.Info("similarity score", "file", img.FileName, "score", score, "has_reference", len(reference) > 0)
	return DecisionContinue, nil
}
