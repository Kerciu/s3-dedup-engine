package dedup

// DedupDecision is the upload routing outcome returned by a pipeline stage.
type DedupDecision int

const (
	DecisionContinue DedupDecision = iota
	DecisionAcceptFull
	DecisionSoftDedup
)

func (d DedupDecision) String() string {
	switch d {
	case DecisionContinue:
		return "continue"
	case DecisionAcceptFull:
		return "accept_full"
	case DecisionSoftDedup:
		return "soft_dedup"
	default:
		return "unknown"
	}
}
