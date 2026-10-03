package dedup

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"os"

	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/storage"
)

type FullHashPipeline struct {
	dynamo storage.DynamoClient
}

func NewFullHashPipeline(dynamo storage.DynamoClient) *FullHashPipeline {
	return &FullHashPipeline{dynamo: dynamo}
}

func (p *FullHashPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	if !img.ChunkMatched {
		slog.Debug("skipping full hash; no chunk candidates", "file", img.FileName)
		return DecisionContinue, nil
	}

	f, err := os.Open(img.FilePath)
	if err != nil {
		return 0, fmt.Errorf("open image for full hash: %w", err)
	}
	defer f.Close()

	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return 0, fmt.Errorf("hash full image: %w", err)
	}
	img.FullHash = hex.EncodeToString(h.Sum(nil))
	slog.Debug("computed full hash", "file", img.FileName, "full_hash", img.FullHash)

	s3Key, found, err := p.dynamo.GetByChunkAndFull(ctx, img.ChunkHash, img.FullHash)
	if err != nil {
		return 0, fmt.Errorf("lookup full hash: %w", err)
	}
	if !found {
		slog.Debug("no bit-identical match; fallback to similarity",
			"file", img.FileName,
			"chunk_hash", img.ChunkHash,
			"full_hash", img.FullHash,
		)
		return DecisionContinue, nil
	}

	img.SoftDedup = true
	if s3Key != "" {
		img.ExistingS3Key = s3Key
	}
	slog.Info("bit-identical duplicate; soft-dedup",
		"file", img.FileName,
		"existing_s3_key", s3Key,
	)
	return DecisionSoftDedup, nil
}
