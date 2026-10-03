package dedup

import (
	"context"
	"fmt"

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
		decision, err := stage.Process(ctx, img)
		if err != nil {
			return 0, fmt.Errorf("dedup stage: %w", err)
		}
		if decision != DecisionContinue {
			if decision == DecisionSoftDedup {
				img.SoftDedup = true
			}
			return decision, nil
		}
	}
	return DecisionAcceptFull, nil
}
