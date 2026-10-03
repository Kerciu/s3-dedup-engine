package dedup

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
)

// SimilarityPipeline is the terminal stage delegating the verdict to the AI worker.
type SimilarityPipeline struct {
	client *grpcclient.DedupClient
}

func NewSimilarityPipeline(client *grpcclient.DedupClient) *SimilarityPipeline {
	return &SimilarityPipeline{client: client}
}

func (p *SimilarityPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	f, err := os.Open(img.FilePath)
	if err != nil {
		return 0, fmt.Errorf("open image for similarity: %w", err)
	}
	defer f.Close()

	result, err := p.client.ProcessImage(ctx, img.FileName, img.SizeBytes, f)
	if err != nil {
		return 0, fmt.Errorf("semantic dedup rpc: %w", err)
	}

	img.QualityScore = result.QualityScore
	img.SemanticDistance = result.Distance
	img.ExistingImageKey = result.ExistingImageKey
	slog.Info("semantic dedup verdict",
		"file", img.FileName,
		"status", result.Status,
		"quality_score", result.QualityScore,
		"distance", result.Distance,
		"existing_image_key", result.ExistingImageKey,
	)

	return applySemanticStatus(img, result.Status)
}

func applySemanticStatus(img *domain.ImageRecord, status string) (DedupDecision, error) {
	switch status {
	case constants.StatusInserted:
		img.SoftDedup = false
		img.ReplaceExisting = false
		return DecisionAcceptFull, nil
	case constants.StatusReplaced:
		img.SoftDedup = false
		img.ReplaceExisting = true
		return DecisionAcceptFull, nil
	case constants.StatusDuplicateRejected:
		img.SoftDedup = true
		img.ReplaceExisting = false
		return DecisionSoftDedup, nil
	default:
		return 0, fmt.Errorf("unknown dedup status %q from ai worker", status)
	}
}
