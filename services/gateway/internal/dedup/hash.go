package dedup

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log"

	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/storage"
)

// HashCollisionPipeline hashes payloads and soft-dedups on exact DynamoDB hits.
type HashCollisionPipeline struct {
	dynamo storage.DynamoClient
	mock   bool
}

func NewHashCollisionPipeline(dynamo storage.DynamoClient) *HashCollisionPipeline {
	return &HashCollisionPipeline{dynamo: dynamo, mock: true}
}

func (p *HashCollisionPipeline) Process(ctx context.Context, ticket *domain.Ticket) (DedupDecision, error) {
	sum := sha256.Sum256(ticket.Payload)
	ticket.FileHash = hex.EncodeToString(sum[:])
	log.Printf("HashCollisionPipeline: ticket %s hash=%s", ticket.ID, ticket.FileHash)

	if p.mock {
		log.Printf("HashCollisionPipeline: mocked DynamoDB miss for hash %s", ticket.FileHash)
		return DecisionContinue, nil
	}

	exists, err := p.dynamo.CheckHashExists(ctx, ticket.FileHash)
	if err != nil {
		return 0, fmt.Errorf("check hash exists: %w", err)
	}
	if exists {
		log.Printf("HashCollisionPipeline: collision for ticket %s; soft-dedup", ticket.ID)
		return DecisionSoftDedup, nil
	}
	return DecisionContinue, nil
}
