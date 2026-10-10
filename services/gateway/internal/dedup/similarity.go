package dedup

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
	"s3-dedup-engine/services/gateway/internal/storage"
)

// SimilarityPipeline is the terminal stage delegating the verdict to the AI worker.
type SimilarityPipeline struct {
	client    *grpcclient.DedupClient
	s3        storage.S3Client
	threshold float64
}

func NewSimilarityPipeline(client *grpcclient.DedupClient, s3 storage.S3Client, threshold float64) *SimilarityPipeline {
	return &SimilarityPipeline{client: client, s3: s3, threshold: threshold}
}

func (p *SimilarityPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	f, err := os.Open(img.FilePath)
	if err != nil {
		return 0, fmt.Errorf("open image for similarity: %w", err)
	}
	defer f.Close()

	result, err := p.client.ProcessImage(ctx, img.FileName, img.SizeBytes, float32(p.threshold), f)
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

	decision, err := applySemanticStatus(img, result.Status)
	if err != nil {
		return 0, err
	}
	if !img.ReplaceExisting {
		return decision, nil
	}
	if result.ExistingImageKey == "" {
		return 0, fmt.Errorf("replaced status missing existing_image_key")
	}
	if p.s3 == nil {
		return 0, fmt.Errorf("s3 client required to measure replaced object")
	}
	size, sizeErr := p.s3.PayloadBytes(ctx, result.ExistingImageKey)
	if sizeErr != nil {
		slog.Debug("could not stat replaced s3 object", "key", result.ExistingImageKey, "error", sizeErr)
	}
	if result.ExistingImageKey == img.FileName {
		if sizeErr == nil && size > img.SizeBytes {
			img.DeletedBytes = size - img.SizeBytes
		}
		slog.Info("replaced object shares the upload key",
			"key", result.ExistingImageKey,
			"net_deleted_bytes", img.DeletedBytes,
		)
		return decision, nil
	}
	if sizeErr == nil {
		img.DeletedBytes = size
	}
	slog.Info("replacement will retire incumbent after upload",
		"key", result.ExistingImageKey,
		"incumbent_bytes", img.DeletedBytes,
	)
	return decision, nil
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
