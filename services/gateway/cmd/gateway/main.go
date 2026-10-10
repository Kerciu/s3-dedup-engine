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
	"io/fs"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/schollz/progressbar/v3"
	"golang.org/x/sync/errgroup"

	"s3-dedup-engine/services/gateway/internal/analytics"
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
		os.Exit(1)
	}
}

func run(ctx context.Context) (err error) {
	var cleanup func()
	defer func() {
		if err != nil {
			slog.Error("gateway failed", "error", err)
		}
		if cleanup != nil {
			cleanup()
		}
	}()

	imagePath := flag.String(constants.FlagImage, "", "path to one image file (.jpg, .png)")
	dirPath := flag.String(constants.FlagDir, "", "directory of images to process")
	workers := flag.Int(constants.FlagWorkers, constants.DefaultWorkers, "maximum concurrent image workers")
	verbose := flag.Bool(constants.FlagVerbose, false, "enable debug logging on the terminal")
	threshold := flag.Float64(constants.FlagThreshold, constants.DefaultCosineThreshold, "maximum cosine distance treated as a near-duplicate")
	flag.Parse()

	if (*imagePath == "") == (*dirPath == "") {
		return fmt.Errorf("provide exactly one of --%s or --%s", constants.FlagImage, constants.FlagDir)
	}
	if *workers < 1 {
		return fmt.Errorf("--%s must be at least 1", constants.FlagWorkers)
	}

	logDir, cleanupFn, setupErr := logging.Setup(*verbose)
	if setupErr != nil {
		return fmt.Errorf("setup logging: %w", setupErr)
	}
	cleanup = cleanupFn

	paths, err := resolvePaths(*imagePath, *dirPath)
	if err != nil {
		return err
	}
	slog.Info("starting dedup run", "images", len(paths), "workers", *workers, "log_dir", logDir)

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
		dedup.NewSimilarityPipeline(dedupClient, clients.S3, *threshold),
	)
	collector := analytics.NewCollector()

	var processErr error
	if *dirPath != "" {
		processErr = processDirectory(ctx, clients, chain, paths, *workers, collector)
	} else {
		img, imgErr := processImage(ctx, clients, chain, paths[0])
		collector.Record(img, imgErr)
		processErr = imgErr
	}

	if reportErr := analytics.Report(logDir, collector.Snapshot()); reportErr != nil {
		return reportErr
	}
	if processErr != nil {
		return processErr
	}
	if failed := collector.Snapshot().FailedCount; failed > 0 {
		return fmt.Errorf("failed to process %d images", failed)
	}
	return nil
}

func resolvePaths(imagePath, dirPath string) ([]string, error) {
	if imagePath != "" {
		return []string{imagePath}, nil
	}
	paths, err := scanImages(dirPath)
	if err != nil {
		return nil, err
	}
	if len(paths) == 0 {
		return nil, fmt.Errorf("no images found in %s", dirPath)
	}
	return paths, nil
}

func scanImages(root string) ([]string, error) {
	var paths []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || !isImage(path) {
			return nil
		}
		paths = append(paths, path)
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("scan %s: %w", root, err)
	}
	return paths, nil
}

func isImage(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".jpg", ".jpeg", ".png":
		return true
	default:
		return false
	}
}

func processDirectory(ctx context.Context, clients *storage.Clients, chain *dedup.DedupChain, paths []string, workers int, collector *analytics.Collector) error {
	unique, collisions := splitBasenameCollisions(paths)
	bar := progressbar.NewOptions(len(paths),
		progressbar.OptionSetWriter(os.Stdout),
		progressbar.OptionSetDescription("dedup"),
		progressbar.OptionShowCount(),
		progressbar.OptionSetWidth(40),
		progressbar.OptionShowIts(),
		progressbar.OptionOnCompletion(func() { fmt.Println() }),
	)
	for _, path := range collisions {
		recordBasenameCollision(collector, path)
		_ = bar.Add(1)
	}

	group, groupCtx := errgroup.WithContext(ctx)
	group.SetLimit(workers)

	var scheduleErr error
	for _, path := range unique {
		if err := groupCtx.Err(); err != nil {
			scheduleErr = err
			break
		}
		path := path
		group.Go(func() error {
			img, procErr := processImage(groupCtx, clients, chain, path)
			collector.Record(img, procErr)
			_ = bar.Add(1)
			if procErr != nil && groupCtx.Err() == nil {
				slog.Error("failed to process image", "path", path, "error", procErr)
			}
			return nil
		})
	}
	waitErr := group.Wait()
	_ = bar.Finish()
	if waitErr != nil {
		return waitErr
	}
	return scheduleErr
}

func splitBasenameCollisions(paths []string) (unique []string, collisions []string) {
	seen := make(map[string]struct{}, len(paths))
	for _, path := range paths {
		name := filepath.Base(path)
		if _, ok := seen[name]; ok {
			collisions = append(collisions, path)
			continue
		}
		seen[name] = struct{}{}
		unique = append(unique, path)
	}
	return unique, collisions
}

func recordBasenameCollision(collector *analytics.Collector, path string) {
	name := filepath.Base(path)
	dupErr := fmt.Errorf("duplicate file name %s", name)
	img, loadErr := loadImageRecord(path)
	if loadErr != nil {
		collector.Record(img, fmt.Errorf("%w: %v", dupErr, loadErr))
		slog.Error("failed to process image", "path", path, "error", dupErr)
		return
	}
	collector.Record(img, dupErr)
	slog.Error("failed to process image", "path", path, "error", dupErr)
}

func processImage(ctx context.Context, clients *storage.Clients, chain *dedup.DedupChain, path string) (*domain.ImageRecord, error) {
	img, err := loadImageRecord(path)
	if err != nil {
		return nil, err
	}
	slog.Debug("loaded image record",
		"file", img.FileName,
		"width", img.Width,
		"height", img.Height,
		"size_bytes", img.SizeBytes,
	)

	decision, err := chain.Execute(ctx, img)
	if err != nil {
		return img, fmt.Errorf("execute dedup chain: %w", err)
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
		return img, fmt.Errorf("marshal metadata: %w", err)
	}
	if err := clients.S3.UploadMetadata(ctx, img.ID+".json", meta); err != nil {
		return img, fmt.Errorf("upload metadata: %w", err)
	}
	slog.Info("uploaded metadata", "id", img.ID)

	if img.SoftDedup {
		slog.Info("soft-dedup skipped image upload", "file", img.FileName)
		return img, nil
	}

	s3Key := constants.S3PayloadKeyPrefix + img.FileName
	if err := uploadImage(ctx, clients, img, s3Key); err != nil {
		return img, err
	}
	slog.Info("uploaded image", "s3_key", s3Key, "replace_existing", img.ReplaceExisting)
	if err := retireReplaced(ctx, clients, img); err != nil {
		return img, err
	}
	return img, nil
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
		ChunkHash: img.ChunkHash,
		FullHash:  img.FullHash,
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

func retireReplaced(ctx context.Context, clients *storage.Clients, img *domain.ImageRecord) error {
	if !img.ReplaceExisting || img.ExistingImageKey == "" || img.ExistingImageKey == img.FileName {
		return nil
	}
	prior, found, err := clients.Dynamo.GetFileByName(ctx, img.ExistingImageKey)
	if err != nil {
		return fmt.Errorf("lookup replaced file: %w", err)
	}
	if err := clients.S3.DeleteObject(ctx, img.ExistingImageKey); err != nil {
		return fmt.Errorf("delete replaced payload: %w", err)
	}
	if err := clients.S3.DeleteMetadata(ctx, img.ExistingImageKey+".json"); err != nil {
		return fmt.Errorf("delete replaced metadata: %w", err)
	}
	if err := clients.Dynamo.DeleteFileByName(ctx, img.ExistingImageKey); err != nil {
		return fmt.Errorf("delete replaced file index: %w", err)
	}
	if found && prior.ChunkHash != "" && prior.FullHash != "" {
		if err := clients.Dynamo.DeleteChunkFull(ctx, prior.ChunkHash, prior.FullHash); err != nil {
			return fmt.Errorf("delete replaced chunk index: %w", err)
		}
	}
	slog.Info("retired replaced object",
		"key", img.ExistingImageKey,
		"deleted_bytes", img.DeletedBytes,
	)
	return nil
}
