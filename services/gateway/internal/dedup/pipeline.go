package dedup

import (
	"context"
	"fmt"
	"time"

	"s3-dedup-engine/services/gateway/internal/domain"
)

// DedupPipeline is a single stage in the visual soft-deduplication chain.
type DedupPipeline interface {
	Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error)
}

// DedupChain runs pipeline stages until a terminal upload decision is reached.
type DedupChain struct {
	stages []DedupPipeline
}

func NewDedupChain(stages ...DedupPipeline) *DedupChain {
	return &DedupChain{stages: stages}
}

func (c *DedupChain) Execute(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	for _, stage := range c.stages {
		started := time.Now()
		decision, err := stage.Process(ctx, img)
		timing := domain.StageTiming{
			Name:       stageName(stage),
			Decision:   decision.String(),
			DurationMs: float64(time.Since(started).Microseconds()) / 1000,
		}
		if err != nil {
			timing.Error = err.Error()
			img.Stages = append(img.Stages, timing)
			return 0, fmt.Errorf("dedup stage: %w", err)
		}
		img.Stages = append(img.Stages, timing)
		if decision != DecisionContinue {
			if decision == DecisionSoftDedup {
				img.SoftDedup = true
			}
			return decision, nil
		}
	}
	return DecisionAcceptFull, nil
}

func stageName(stage DedupPipeline) string {
	switch stage.(type) {
	case *FileNameMatchPipeline:
		return "file_name"
	case *ChunkPipeline:
		return "chunk"
	case *FullHashPipeline:
		return "full_hash"
	case *SimilarityPipeline:
		return "similarity"
	default:
		return fmt.Sprintf("%T", stage)
	}
}
