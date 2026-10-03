package domain

// Ticket is the helpdesk unit processed by the deduplication gateway.
type Ticket struct {
	ID               string
	Text             string
	Payload          []byte
	FileHash         string
	AttachmentSizeMB float64
	SoftDedup        bool
}
