package domain

// ImageRecord is the visual asset unit processed by the deduplication gateway.
type ImageRecord struct {
	ID              string  `json:"id"`
	FilePath        string  `json:"-"`
	FileName        string  `json:"file_name"`
	Width           int     `json:"width"`
	Height          int     `json:"height"`
	SizeBytes       int64   `json:"size_bytes"`
	ChunkHash       string  `json:"chunk_hash,omitempty"`
	FullHash        string  `json:"full_hash,omitempty"`
	NameMatched     bool    `json:"-"`
	ChunkMatched    bool    `json:"-"`
	SoftDedup       bool    `json:"soft_dedup"`
	ReplaceExisting bool    `json:"replace_existing"`
	ExistingS3Key   string  `json:"-"`
	PriorWidth      int     `json:"-"`
	PriorHeight     int     `json:"-"`
	PriorSizeBytes  int64   `json:"-"`
	SimilarityScore float32 `json:"similarity_score,omitempty"`
}
