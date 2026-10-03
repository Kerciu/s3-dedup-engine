package dedup

import (
	"context"
	"fmt"

	"s3-dedup-engine/services/gateway/internal/domain"
)

// DedupPipeline is a single stage in the soft-deduplication chain.
type DedupPipeline interface {
	Process(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error)
}

// DedupChain runs pipeline stages until a terminal upload decision is reached.
type DedupChain struct {
	stages []DedupPipeline
}

func NewDedupChain(stages ...DedupPipeline) *DedupChain {
	return &DedupChain{stages: stages}
}

func (c *DedupChain) Execute(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error) {
	for _, stage := range c.stages {
		decision, err := stage.Process(ctx, ticket)
		if err != nil {
			return 0, fmt.Errorf("dedup stage: %w", err)
		}
		if decision != DecisionContinue {
			if decision == DecisionSoftDedup {
				ticket.SoftDedup = true
				ticket.Payload = nil
			}
			return decision, nil
		}
	}
	return DecisionAcceptFull, nil
}
