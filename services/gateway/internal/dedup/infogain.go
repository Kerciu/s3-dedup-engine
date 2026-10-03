package dedup

import (
	"context"
	"log/slog"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
)

type InfoGainPipeline struct{}

func NewInfoGainPipeline() *InfoGainPipeline {
	return &InfoGainPipeline{}
}

func (p *InfoGainPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	_ = ctx
	if img.SimilarityScore < constants.SimilarityThreshold {
		img.SoftDedup = false
		img.ReplaceExisting = false
		slog.Info("image unique enough; accept full",
			"file", img.FileName,
			"score", img.SimilarityScore,
			"threshold", constants.SimilarityThreshold,
		)
		return DecisionAcceptFull, nil
	}

	newPixels := int64(img.Width) * int64(img.Height)
	oldPixels := int64(img.PriorWidth) * int64(img.PriorHeight)
	better := newPixels > oldPixels || (newPixels == oldPixels && img.SizeBytes > img.PriorSizeBytes)
	slog.Debug("info gain quality comparison",
		"file", img.FileName,
		"new_pixels", newPixels,
		"old_pixels", oldPixels,
		"new_size_bytes", img.SizeBytes,
		"old_size_bytes", img.PriorSizeBytes,
		"new_is_better", better,
	)

	if better {
		img.SoftDedup = false
		img.ReplaceExisting = true
		slog.Info("semantic duplicate with higher quality; replace existing", "file", img.FileName)
		return DecisionAcceptFull, nil
	}

	img.SoftDedup = true
	img.ReplaceExisting = false
	slog.Info("semantic duplicate with equal or worse quality; soft-dedup", "file", img.FileName)
	return DecisionSoftDedup, nil
}
