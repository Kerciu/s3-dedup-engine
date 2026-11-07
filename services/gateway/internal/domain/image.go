package domain

// StageTiming is one pipeline stage measurement for a single image.
type StageTiming struct {
	Name       string  `json:"name"`
	Decision   string  `json:"decision"`
	DurationMs float64 `json:"duration_ms"`
	Error      string  `json:"error,omitempty"`
}

// ImageRecord is the visual asset unit processed by the deduplication gateway.
type ImageRecord struct {
	ID               string        `json:"id"`
	FilePath         string        `json:"-"`
	FileName         string        `json:"file_name"`
	Width            int           `json:"width"`
	Height           int           `json:"height"`
	SizeBytes        int64         `json:"size_bytes"`
	ChunkHash        string        `json:"chunk_hash,omitempty"`
	FullHash         string        `json:"full_hash,omitempty"`
	NameMatched      bool          `json:"-"`
	ChunkMatched     bool          `json:"-"`
	SoftDedup        bool          `json:"soft_dedup"`
	ReplaceExisting  bool          `json:"replace_existing"`
	ExistingS3Key    string        `json:"-"`
	QualityScore     float32       `json:"quality_score,omitempty"`
	SemanticDistance float32       `json:"semantic_distance,omitempty"`
	ExistingImageKey string        `json:"existing_image_key,omitempty"`
	DeletedBytes     int64         `json:"-"`
	Stages           []StageTiming `json:"-"`
	UploadSkipped    bool          `json:"-"`
	UploadBytes      int64         `json:"-"`
	RetireBytes      int64         `json:"-"`
}
