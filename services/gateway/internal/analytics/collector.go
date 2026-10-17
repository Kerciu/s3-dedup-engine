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
	TotalProcessedCount     int64   `json:"total_processed_count" yaml:"total_processed_count"`
	TotalProcessedBytes     int64   `json:"total_processed_bytes" yaml:"total_processed_bytes"`
	UploadedUniqueCount     int64   `json:"uploaded_unique_count" yaml:"uploaded_unique_count"`
	UploadedUniqueBytes     int64   `json:"uploaded_unique_bytes" yaml:"uploaded_unique_bytes"`
	HashChunkDuplicateCount int64   `json:"full_hash_duplicate_count" yaml:"full_hash_duplicate_count"`
	HashChunkSavedBytes     int64   `json:"full_hash_saved_bytes" yaml:"full_hash_saved_bytes"`
	SemanticDuplicateCount  int64   `json:"semantic_duplicate_count" yaml:"semantic_duplicate_count"`
	SemanticSavedBytes      int64   `json:"semantic_saved_bytes" yaml:"semantic_saved_bytes"`
	ReplacedCount           int64   `json:"replaced_count" yaml:"replaced_count"`
	ReplacedDeletedBytes    int64   `json:"replaced_deleted_bytes" yaml:"replaced_deleted_bytes"`
	FailedCount             int64   `json:"failed_count" yaml:"failed_count"`
	FailedBytes             int64   `json:"failed_bytes" yaml:"failed_bytes"`
	BytesNotUploaded        int64   `json:"bytes_not_uploaded" yaml:"bytes_not_uploaded"`
	BytesNotUploadedPercent float64 `json:"bytes_not_uploaded_percent" yaml:"bytes_not_uploaded_percent"`
	BytesRemovedFromBucket  int64   `json:"bytes_removed_from_bucket" yaml:"bytes_removed_from_bucket"`
	TotalStorageSavedBytes  int64   `json:"total_storage_saved_bytes" yaml:"total_storage_saved_bytes"`
	AIBypassRatePercent     float64 `json:"ai_bypass_rate_percent" yaml:"ai_bypass_rate_percent"`
	ElapsedSeconds          float64 `json:"elapsed_seconds" yaml:"elapsed_seconds"`
	ImagesPerSecond         float64 `json:"images_per_second" yaml:"images_per_second"`
	MegabytesPerSecond      float64 `json:"megabytes_per_second" yaml:"megabytes_per_second"`
	EstimatedMonthlyUSD     float64 `json:"estimated_monthly_savings_usd" yaml:"estimated_monthly_savings_usd"`
}

// Collector accumulates per-image outcomes from concurrent workers.
type Collector struct {
	mu        sync.Mutex
	started   time.Time
	events    *EventWriter
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

func NewCollectorWithEvents(dir string) (*Collector, error) {
	writer, err := NewEventWriter(dir)
	if err != nil {
		return nil, err
	}
	return &Collector{started: time.Now(), events: writer}, nil
}

func (c *Collector) Close() error {
	if c == nil || c.events == nil {
		return nil
	}
	return c.events.Close()
}

func (c *Collector) Record(img *domain.ImageRecord, procErr error) {
	c.mu.Lock()
	var size int64
	if img != nil {
		size = img.SizeBytes
	}
	c.totalN++
	c.totalB += size
	if procErr != nil || img == nil {
		c.failedN++
		c.failedB += size
	} else {
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
	event := eventFrom(img, procErr)
	c.mu.Unlock()

	if c.events != nil {
		_ = c.events.Write(event, c.Snapshot())
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
