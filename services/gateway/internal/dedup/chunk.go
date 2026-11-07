package dedup

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"os"

	"github.com/restic/chunker"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/storage"
)

type ChunkPipeline struct {
	dynamo storage.DynamoClient
}

func NewChunkPipeline(dynamo storage.DynamoClient) *ChunkPipeline {
	return &ChunkPipeline{dynamo: dynamo}
}

func (p *ChunkPipeline) Process(ctx context.Context, img *domain.ImageRecord) (DedupDecision, error) {
	f, err := os.Open(img.FilePath)
	if err != nil {
		return 0, fmt.Errorf("open image for chunking: %w", err)
	}
	defer f.Close()

	chunkBytes, err := readFirstChunk(f)
	if err != nil {
		return 0, err
	}
	sum := sha256.Sum256(chunkBytes)
	img.ChunkHash = hex.EncodeToString(sum[:])
	slog.Debug("computed chunk hash",
		"file", img.FileName,
		"chunk_hash", img.ChunkHash,
		"chunk_bytes", len(chunkBytes),
	)

	found, err := p.dynamo.QueryChunkCandidates(ctx, img.ChunkHash)
	if err != nil {
		return 0, fmt.Errorf("query chunk candidates: %w", err)
	}
	img.ChunkMatched = found
	if found {
		slog.Info("chunk candidates found", "file", img.FileName, "chunk_hash", img.ChunkHash)
	} else {
		slog.Debug("no chunk candidates", "file", img.FileName, "chunk_hash", img.ChunkHash)
	}
	return DecisionContinue, nil
}

func readFirstChunk(r io.ReadSeeker) ([]byte, error) {
	poly := chunker.Pol(constants.ChunkerPolynomial)
	if poly.Deg() > 53 {
		slog.Debug("invalid chunker polynomial degree; using fallback read", "degree", poly.Deg())
		return readFallbackChunk(r)
	}

	ch := chunker.New(r, poly)
	buf := make([]byte, constants.ChunkFallbackBytes)
	c, err := ch.Next(buf)
	if err == nil && len(c.Data) > 0 {
		return c.Data, nil
	}
	slog.Debug("chunker next failed or empty; using fallback read", "error", err)
	return readFallbackChunk(r)
}

func readFallbackChunk(r io.ReadSeeker) ([]byte, error) {
	if _, seekErr := r.Seek(0, io.SeekStart); seekErr != nil {
		return nil, fmt.Errorf("seek for chunk fallback: %w", seekErr)
	}
	buf := make([]byte, constants.ChunkFallbackBytes)
	n, readErr := io.ReadFull(r, buf)
	if readErr != nil && readErr != io.EOF && readErr != io.ErrUnexpectedEOF {
		return nil, fmt.Errorf("read chunk fallback: %w", readErr)
	}
	if n == 0 {
		return nil, fmt.Errorf("empty image for chunking")
	}
	return buf[:n], nil
}
