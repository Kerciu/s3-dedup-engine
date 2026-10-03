package dedup

import (
	"context"
	"log"

	"s3-dedup-engine/services/gateway/internal/domain"
)

// ChunkPipeline mocks Content-Defined Chunking with Rabin-Karp.
type ChunkPipeline struct{}

func NewChunkPipeline() *ChunkPipeline {
	return &ChunkPipeline{}
}

func (p *ChunkPipeline) Process(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error) {
	_ = ctx
	log.Printf("ChunkPipeline: simulating Rabin-Karp CDC for ticket %s (%d bytes)", ticket.ID, len(ticket.Payload))
	return DecisionContinue, nil
}
