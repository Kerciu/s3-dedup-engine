package analytics

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"s3-dedup-engine/services/gateway/internal/constants"
	"s3-dedup-engine/services/gateway/internal/domain"
)

// ImageEvent is one processed image keyed by file name.
type ImageEvent struct {
	File             string               `json:"file"`
	Outcome          string               `json:"outcome"`
	ExistingImageKey string               `json:"existing_image_key,omitempty"`
	SemanticDistance float32              `json:"semantic_distance,omitempty"`
	QualityScore     float32              `json:"quality_score,omitempty"`
	SizeBytes        int64                `json:"size_bytes"`
	UploadSkipped    bool                 `json:"upload_skipped,omitempty"`
	UploadBytes      int64                `json:"upload_bytes,omitempty"`
	RetireBytes      int64                `json:"retire_bytes,omitempty"`
	Stages           []domain.StageTiming `json:"stages,omitempty"`
	Error            string               `json:"error,omitempty"`
}

// EventWriter appends per-image JSONL and rewrites the run summary.
type EventWriter struct {
	mu       sync.Mutex
	events   *os.File
	dir      string
	disabled bool
}

func NewEventWriter(dir string) (*EventWriter, error) {
	if dir == "" {
		return &EventWriter{disabled: true}, nil
	}
	eventsPath := filepath.Join(dir, constants.EventsFileName)
	f, err := os.OpenFile(eventsPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open events file: %w", err)
	}
	return &EventWriter{events: f, dir: dir}, nil
}

func (w *EventWriter) Close() error {
	if w == nil || w.disabled || w.events == nil {
		return nil
	}
	return w.events.Close()
}

func (w *EventWriter) Write(event ImageEvent, snap Snapshot) error {
	if w == nil || w.disabled {
		return nil
	}
	w.mu.Lock()
	defer w.mu.Unlock()

	body, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal image event: %w", err)
	}
	if _, err := w.events.Write(append(body, '\n')); err != nil {
		return fmt.Errorf("append image event: %w", err)
	}
	if err := w.events.Sync(); err != nil {
		return fmt.Errorf("sync events file: %w", err)
	}

	summary, err := json.MarshalIndent(snap, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal summary: %w", err)
	}
	summary = append(summary, '\n')
	path := filepath.Join(w.dir, constants.SummaryFileName)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, summary, 0o644); err != nil {
		return fmt.Errorf("write summary temp: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("rename summary: %w", err)
	}
	return nil
}

func outcomeOf(img *domain.ImageRecord, procErr error) string {
	if procErr != nil || img == nil {
		return constants.OutcomeFailed
	}
	switch {
	case img.ReplaceExisting:
		return constants.OutcomeReplaced
	case img.SoftDedup && img.ExistingImageKey != "":
		return constants.OutcomeSemanticDuplicate
	case img.SoftDedup:
		return constants.OutcomeHashDuplicate
	default:
		return constants.OutcomeInserted
	}
}

func eventFrom(img *domain.ImageRecord, procErr error) ImageEvent {
	event := ImageEvent{Outcome: outcomeOf(img, procErr)}
	if procErr != nil {
		event.Error = procErr.Error()
	}
	if img == nil {
		return event
	}
	event.File = img.FileName
	event.ExistingImageKey = img.ExistingImageKey
	event.SemanticDistance = img.SemanticDistance
	event.QualityScore = img.QualityScore
	event.SizeBytes = img.SizeBytes
	event.UploadSkipped = img.UploadSkipped
	event.UploadBytes = img.UploadBytes
	event.RetireBytes = img.RetireBytes
	event.Stages = img.Stages
	return event
}
