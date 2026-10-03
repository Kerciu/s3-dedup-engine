package dedup

import (
	"context"
	"fmt"
	"log"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
)

// SimilarityPipeline scores semantic duplicates and branches into InfoGain when needed.
type SimilarityPipeline struct {
	client   *grpcclient.SimilarityClient
	infoGain *InfoGainPipeline
}

func NewSimilarityPipeline(client *grpcclient.SimilarityClient, infoGain *InfoGainPipeline) *SimilarityPipeline {
	return &SimilarityPipeline{client: client, infoGain: infoGain}
}

func (p *SimilarityPipeline) Process(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error) {
	score, err := p.client.CheckSimilarity(ctx, ticket.Text)
	if err != nil {
		return 0, fmt.Errorf("check similarity: %w", err)
	}
	log.Printf("SimilarityPipeline: ticket %s score=%.4f threshold=%.2f", ticket.ID, score, constants.SimilarityThreshold)

	if score < constants.SimilarityThreshold {
		log.Printf("SimilarityPipeline: no semantic duplicate for ticket %s; accept full upload", ticket.ID)
		return DecisionAcceptFull, nil
	}

	log.Printf("SimilarityPipeline: semantic duplicate for ticket %s; running InfoGain", ticket.ID)
	return p.infoGain.Process(ctx, ticket)
}
