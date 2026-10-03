package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"s3-dedup-engine/services/gateway/internal/dedup"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
	"s3-dedup-engine/services/gateway/internal/storage"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := run(ctx); err != nil {
		log.Fatalf("gateway failed: %v", err)
	}
}

func run(ctx context.Context) error {
	clients, err := storage.NewClients(ctx)
	if err != nil {
		return fmt.Errorf("init storage: %w", err)
	}

	simClient, err := grpcclient.DialSimilarity(ctx)
	if err != nil {
		return fmt.Errorf("init grpc client: %w", err)
	}
	defer simClient.Close()

	infoGain := dedup.NewInfoGainPipeline()
	chain := dedup.NewDedupChain(
		dedup.NewChunkPipeline(),
		dedup.NewHashCollisionPipeline(clients.Dynamo),
		dedup.NewSimilarityPipeline(simClient, infoGain),
	)

	ticket := &domain.Ticket{
		ID:               "TICKET-001",
		Text:             "VPN connection drops after sleep on Windows 11 laptop",
		Payload:          []byte("mock-memory-dump-payload-content"),
		AttachmentSizeMB: 42.0,
	}

	decision, err := chain.Execute(ctx, ticket)
	if err != nil {
		return fmt.Errorf("execute dedup chain: %w", err)
	}
	log.Printf("dedup decision for %s: %s", ticket.ID, decision)

	meta, err := json.Marshal(map[string]any{
		"id":                 ticket.ID,
		"text":               ticket.Text,
		"file_hash":          ticket.FileHash,
		"attachment_size_mb": ticket.AttachmentSizeMB,
		"soft_dedup":         ticket.SoftDedup,
	})
	if err != nil {
		return fmt.Errorf("marshal metadata: %w", err)
	}

	switch decision {
	case dedup.DecisionAcceptFull:
		if err := clients.S3.UploadPayload(ctx, ticket.ID, ticket.Payload); err != nil {
			return fmt.Errorf("upload payload: %w", err)
		}
		if err := clients.S3.UploadMetadata(ctx, ticket.ID+".json", meta); err != nil {
			return fmt.Errorf("upload metadata: %w", err)
		}
		log.Printf("uploaded full ticket %s (payload + metadata)", ticket.ID)
	case dedup.DecisionSoftDedup:
		if err := clients.S3.UploadMetadata(ctx, ticket.ID+".json", meta); err != nil {
			return fmt.Errorf("upload metadata: %w", err)
		}
		log.Printf("soft-dedup ticket %s (metadata only)", ticket.ID)
	default:
		return fmt.Errorf("unexpected decision: %s", decision)
	}

	return nil
}
