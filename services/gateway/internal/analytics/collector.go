package analytics

import (
	"math"
	"sync"
	"time"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
)

// Snapshot is the immutable FinOps summary of one CLI run.
type Snapshot struct {
	TotalProcessedCount     int64   `yaml:"total_processed_count"`
	TotalProcessedBytes     int64   `yaml:"total_processed_bytes"`
	UploadedUniqueCount     int64   `yaml:"uploaded_unique_count"`
	UploadedUniqueBytes     int64   `yaml:"uploaded_unique_bytes"`
	HashChunkDuplicateCount int64   `yaml:"full_hash_duplicate_count"`
	HashChunkSavedBytes     int64   `yaml:"full_hash_saved_bytes"`
	SemanticDuplicateCount  int64   `yaml:"semantic_duplicate_count"`
	SemanticSavedBytes      int64   `yaml:"semantic_saved_bytes"`
	ReplacedCount           int64   `yaml:"replaced_count"`
	ReplacedDeletedBytes    int64   `yaml:"replaced_deleted_bytes"`
	FailedCount             int64   `yaml:"failed_count"`
	FailedBytes             int64   `yaml:"failed_bytes"`
	BytesNotUploaded        int64   `yaml:"bytes_not_uploaded"`
	BytesNotUploadedPercent float64 `yaml:"bytes_not_uploaded_percent"`
	BytesRemovedFromBucket  int64   `yaml:"bytes_removed_from_bucket"`
	TotalStorageSavedBytes  int64   `yaml:"total_storage_saved_bytes"`
	AIBypassRatePercent     float64 `yaml:"ai_bypass_rate_percent"`
	ElapsedSeconds          float64 `yaml:"elapsed_seconds"`
	ImagesPerSecond         float64 `yaml:"images_per_second"`
	MegabytesPerSecond      float64 `yaml:"megabytes_per_second"`
	EstimatedMonthlyUSD     float64 `yaml:"estimated_monthly_savings_usd"`
}

// Collector accumulates per-image outcomes from concurrent workers.
type Collector struct {
	mu        sync.Mutex
	started   time.Time
	totalN    int64
	totalB    int64
	uniqueN   int64
	uniqueB   int64
	hashN     int64
	hashB     int64
	semanticN int64
	semanticB int64
	replacedN int64
	replacedB int64
	failedN   int64
	failedB   int64
}

func NewCollector() *Collector {
	return &Collector{started: time.Now()}
}

func (c *Collector) Record(img *domain.ImageRecord, procErr error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	var size int64
	if img != nil {
		size = img.SizeBytes
	}
	c.totalN++
	c.totalB += size
	if procErr != nil || img == nil {
		c.failedN++
		c.failedB += size
		return
	}
	switch {
	case img.ReplaceExisting:
		c.replacedN++
		c.replacedB += img.DeletedBytes
	case img.SoftDedup && img.ExistingImageKey != "":
		c.semanticN++
		c.semanticB += size
	case img.SoftDedup:
		c.hashN++
		c.hashB += size
	default:
		c.uniqueN++
		c.uniqueB += size
	}
}

func (c *Collector) Snapshot() Snapshot {
	c.mu.Lock()
	defer c.mu.Unlock()

	elapsed := time.Since(c.started).Seconds()
	if elapsed < 0 {
		elapsed = 0
	}
	notUploaded := c.hashB + c.semanticB
	removed := c.replacedB
	successfulBytes := c.totalB - c.failedB
	var notUploadedPct float64
	if successfulBytes > 0 {
		notUploadedPct = float64(notUploaded) / float64(successfulBytes) * 100
	}
	completed := c.totalN - c.failedN
	var bypass float64
	if completed > 0 {
		bypass = float64(c.hashN) / float64(completed) * 100
	}
	var imagesPerSec float64
	var mbPerSec float64
	if elapsed > 0 {
		imagesPerSec = float64(c.totalN) / elapsed
		mbPerSec = float64(c.totalB) / (1024 * 1024) / elapsed
	}
	usd := float64(notUploaded+removed) / float64(constants.BytesPerGiB) * constants.S3StandardUSDPerGBMonth
	return Snapshot{
		TotalProcessedCount:     c.totalN,
		TotalProcessedBytes:     c.totalB,
		UploadedUniqueCount:     c.uniqueN,
		UploadedUniqueBytes:     c.uniqueB,
		HashChunkDuplicateCount: c.hashN,
		HashChunkSavedBytes:     c.hashB,
		SemanticDuplicateCount:  c.semanticN,
		SemanticSavedBytes:      c.semanticB,
		ReplacedCount:           c.replacedN,
		ReplacedDeletedBytes:    c.replacedB,
		FailedCount:             c.failedN,
		FailedBytes:             c.failedB,
		BytesNotUploaded:        notUploaded,
		BytesNotUploadedPercent: round(notUploadedPct, 100),
		BytesRemovedFromBucket:  removed,
		TotalStorageSavedBytes:  notUploaded + removed,
		AIBypassRatePercent:     round(bypass, 100),
		ElapsedSeconds:          round(elapsed, 1000),
		ImagesPerSecond:         round(imagesPerSec, 100),
		MegabytesPerSecond:      round(mbPerSec, 100),
		EstimatedMonthlyUSD:     round(usd, 1_000_000),
	}
}

func round(value float64, scale float64) float64 {
	return math.Round(value*scale) / scale
}
