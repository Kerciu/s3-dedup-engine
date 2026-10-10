package analytics

import (
	"fmt"
	"sync"
	"testing"

	"s3-dedup-engine/services/gateway/internal/domain"
)

func TestRecordClassification(t *testing.T) {
	collector := NewCollector()
	collector.Record(&domain.ImageRecord{SizeBytes: 100}, nil)
	collector.Record(&domain.ImageRecord{SizeBytes: 50, SoftDedup: true}, nil)
	collector.Record(&domain.ImageRecord{SizeBytes: 40, SoftDedup: true, ExistingImageKey: "kept.jpg"}, nil)
	collector.Record(&domain.ImageRecord{SizeBytes: 80, ReplaceExisting: true, DeletedBytes: 70}, nil)
	collector.Record(&domain.ImageRecord{SizeBytes: 10}, fmt.Errorf("unreadable"))

	snap := collector.Snapshot()
	if snap.TotalProcessedCount != 5 || snap.TotalProcessedBytes != 280 {
		t.Fatalf("total = %d images / %d bytes", snap.TotalProcessedCount, snap.TotalProcessedBytes)
	}
	if snap.UploadedUniqueCount != 1 || snap.UploadedUniqueBytes != 100 {
		t.Fatalf("unique = %d / %d", snap.UploadedUniqueCount, snap.UploadedUniqueBytes)
	}
	if snap.HashChunkDuplicateCount != 1 || snap.HashChunkSavedBytes != 50 {
		t.Fatalf("hash = %d / %d", snap.HashChunkDuplicateCount, snap.HashChunkSavedBytes)
	}
	if snap.SemanticDuplicateCount != 1 || snap.SemanticSavedBytes != 40 {
		t.Fatalf("semantic = %d / %d", snap.SemanticDuplicateCount, snap.SemanticSavedBytes)
	}
	if snap.ReplacedCount != 1 || snap.ReplacedDeletedBytes != 70 {
		t.Fatalf("replaced = %d / %d", snap.ReplacedCount, snap.ReplacedDeletedBytes)
	}
	if snap.FailedCount != 1 || snap.FailedBytes != 10 {
		t.Fatalf("failed = %d / %d", snap.FailedCount, snap.FailedBytes)
	}
	if snap.BytesNotUploaded != 90 || snap.BytesRemovedFromBucket != 70 || snap.TotalStorageSavedBytes != 160 {
		t.Fatalf("saved split = not uploaded %d removed %d total %d", snap.BytesNotUploaded, snap.BytesRemovedFromBucket, snap.TotalStorageSavedBytes)
	}
	if snap.BytesNotUploadedPercent > 100 {
		t.Fatalf("not-uploaded percent = %v", snap.BytesNotUploadedPercent)
	}
	parts := snap.UploadedUniqueCount + snap.HashChunkDuplicateCount + snap.SemanticDuplicateCount + snap.ReplacedCount + snap.FailedCount
	if parts != snap.TotalProcessedCount {
		t.Fatalf("counts do not add up: %d != %d", parts, snap.TotalProcessedCount)
	}
}

func TestRecordConcurrent(t *testing.T) {
	collector := NewCollector()
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			collector.Record(&domain.ImageRecord{SizeBytes: 1, SoftDedup: true}, nil)
		}()
	}
	wg.Wait()
	snap := collector.Snapshot()
	if snap.TotalProcessedCount != 100 || snap.HashChunkDuplicateCount != 100 || snap.HashChunkSavedBytes != 100 {
		t.Fatalf("concurrent snapshot = %+v", snap)
	}
}

func TestReplacementRemovalDoesNotInflateUploadPercent(t *testing.T) {
	collector := NewCollector()
	collector.Record(&domain.ImageRecord{SizeBytes: 100, ReplaceExisting: true, DeletedBytes: 10_000_000}, nil)
	snap := collector.Snapshot()
	if snap.BytesNotUploaded != 0 || snap.BytesNotUploadedPercent != 0 {
		t.Fatalf("not uploaded = %d / %v%%", snap.BytesNotUploaded, snap.BytesNotUploadedPercent)
	}
	if snap.BytesRemovedFromBucket != 10_000_000 || snap.TotalStorageSavedBytes != 10_000_000 {
		t.Fatalf("removed = %d total %d", snap.BytesRemovedFromBucket, snap.TotalStorageSavedBytes)
	}
}
