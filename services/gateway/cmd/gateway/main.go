package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/dedup"
	"s3-dedup-engine/services/gateway/internal/domain"
	"s3-dedup-engine/services/gateway/internal/grpcclient"
	"s3-dedup-engine/services/gateway/internal/logging"
	"s3-dedup-engine/services/gateway/internal/storage"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := run(ctx); err != nil {
		slog.Error("gateway failed", "error", err)
		os.Exit(1)
	}
}

func run(ctx context.Context) error {
	imagePath := flag.String(constants.FlagImage, "", "path to the image file (.jpg, .png)")
	verbose := flag.Bool(constants.FlagVerbose, false, "enable debug logging")
	flag.Parse()
	logging.Setup(*verbose)

	if *imagePath == "" {
		return fmt.Errorf("--%s is required", constants.FlagImage)
	}

	img, err := loadImageRecord(*imagePath)
	if err != nil {
		return err
	}
	slog.Debug("loaded image record",
		"file", img.FileName,
		"width", img.Width,
		"height", img.Height,
		"size_bytes", img.SizeBytes,
	)

	clients, err := storage.NewClients(ctx)
	if err != nil {
		return fmt.Errorf("init storage: %w", err)
	}

	dedupClient, err := grpcclient.DialDedup()
	if err != nil {
		return fmt.Errorf("init grpc client: %w", err)
	}
	defer dedupClient.Close()

	chain := dedup.NewDedupChain(
		dedup.NewFileNameMatchPipeline(clients.Dynamo),
		dedup.NewChunkPipeline(clients.Dynamo),
		dedup.NewFullHashPipeline(clients.Dynamo),
		dedup.NewSimilarityPipeline(dedupClient),
	)

	decision, err := chain.Execute(ctx, img)
	if err != nil {
		return fmt.Errorf("execute dedup chain: %w", err)
	}
	slog.Info("dedup decision",
		"file", img.FileName,
		"decision", decision.String(),
		"soft_dedup", img.SoftDedup,
		"replace_existing", img.ReplaceExisting,
		"quality_score", img.QualityScore,
		"semantic_distance", img.SemanticDistance,
	)

	meta, err := json.Marshal(img)
	if err != nil {
		return fmt.Errorf("marshal metadata: %w", err)
	}
	if err := clients.S3.UploadMetadata(ctx, img.ID+".json", meta); err != nil {
		return fmt.Errorf("upload metadata: %w", err)
	}
	slog.Info("uploaded metadata", "id", img.ID)

	if img.SoftDedup {
		slog.Info("soft-dedup skipped image upload", "file", img.FileName)
		return nil
	}

	s3Key := constants.S3PayloadKeyPrefix + img.FileName
	if err := uploadImage(ctx, clients, img, s3Key); err != nil {
		return err
	}
	slog.Info("uploaded image", "s3_key", s3Key, "replace_existing", img.ReplaceExisting)
	return nil
}

func loadImageRecord(path string) (*domain.ImageRecord, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open image: %w", err)
	}
	defer f.Close()

	stat, err := f.Stat()
	if err != nil {
		return nil, fmt.Errorf("stat image: %w", err)
	}

	cfg, _, err := image.DecodeConfig(f)
	if err != nil {
		return nil, fmt.Errorf("decode image config: %w", err)
	}

	fileName := filepath.Base(path)
	return &domain.ImageRecord{
		ID:        fileName,
		FilePath:  path,
		FileName:  fileName,
		Width:     cfg.Width,
		Height:    cfg.Height,
		SizeBytes: stat.Size(),
	}, nil
}

func uploadImage(ctx context.Context, clients *storage.Clients, img *domain.ImageRecord, s3Key string) error {
	f, err := os.Open(img.FilePath)
	if err != nil {
		return fmt.Errorf("open image for upload: %w", err)
	}
	defer f.Close()

	if img.FullHash == "" {
		h := sha256.New()
		if _, err := io.Copy(h, f); err != nil {
			return fmt.Errorf("hash image before upload: %w", err)
		}
		img.FullHash = hex.EncodeToString(h.Sum(nil))
		slog.Debug("computed full hash during upload", "file", img.FileName, "full_hash", img.FullHash)
		if _, err := f.Seek(0, io.SeekStart); err != nil {
			return fmt.Errorf("seek image for upload: %w", err)
		}
	}
	if img.ChunkHash == "" {
		chunkBytes, chunkErr := readChunkForIndex(f)
		if chunkErr == nil {
			sum := sha256.Sum256(chunkBytes)
			img.ChunkHash = hex.EncodeToString(sum[:])
			slog.Debug("computed chunk hash during upload", "file", img.FileName, "chunk_hash", img.ChunkHash)
		}
		if _, err := f.Seek(0, io.SeekStart); err != nil {
			return fmt.Errorf("seek image for upload: %w", err)
		}
	}

	if err := clients.S3.UploadPayload(ctx, img.FileName, f); err != nil {
		return fmt.Errorf("upload payload: %w", err)
	}

	if err := clients.Dynamo.PutFileByName(ctx, img.FileName, storage.FileMeta{
		S3Key:     s3Key,
		Width:     img.Width,
		Height:    img.Height,
		SizeBytes: img.SizeBytes,
	}); err != nil {
		return fmt.Errorf("index file name: %w", err)
	}
	slog.Debug("indexed file name in dynamodb", "file", img.FileName, "s3_key", s3Key)

	if img.ChunkHash != "" && img.FullHash != "" {
		if err := clients.Dynamo.PutChunkFull(ctx, img.ChunkHash, img.FullHash, s3Key, img.FileName); err != nil {
			return fmt.Errorf("index chunk+full: %w", err)
		}
		slog.Debug("indexed chunk+full in dynamodb",
			"chunk_hash", img.ChunkHash,
			"full_hash", img.FullHash,
		)
	}
	return nil
}

func readChunkForIndex(r io.ReadSeeker) ([]byte, error) {
	buf := make([]byte, constants.ChunkFallbackBytes)
	n, err := io.ReadFull(r, buf)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return nil, err
	}
	return buf[:n], nil
}
