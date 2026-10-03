package dedup

import (
	"context"
	"log"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
)

// InfoGainPipeline mocks whether a semantic near-duplicate still warrants a full upload.
type InfoGainPipeline struct{}

func NewInfoGainPipeline() *InfoGainPipeline {
	return &InfoGainPipeline{}
}

func (p *InfoGainPipeline) Process(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error) {
	_ = ctx
	score := constants.MockInfoGainScore
	log.Printf("InfoGainPipeline: ticket %s mock info-gain=%.2f", ticket.ID, score)
	if score < constants.MockInfoGainThreshold {
		log.Printf("InfoGainPipeline: low info-gain for ticket %s; soft-dedup", ticket.ID)
		return DecisionSoftDedup, nil
	}
	log.Printf("InfoGainPipeline: high info-gain for ticket %s; accept full upload", ticket.ID)
	return DecisionAcceptFull, nil
}
