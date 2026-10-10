package analytics

import (
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	"github.com/jedib0t/go-pretty/v6/table"
	"gopkg.in/yaml.v3"

	"s3-dedup-engine/services/gateway/internal/constants"
)

func Report(dir string, snap Snapshot) error {
	path := filepath.Join(dir, constants.MetricsFileName)
	body, err := yaml.Marshal(snap)
	if err != nil {
		return fmt.Errorf("marshal metrics: %w", err)
	}
	if err := os.WriteFile(path, body, 0o644); err != nil {
		return fmt.Errorf("write metrics: %w", err)
	}
	PrintTable(snap)
	slog.Info("wrote run metrics", "path", path)
	return nil
}

func PrintTable(snap Snapshot) {
	writer := table.NewWriter()
	writer.SetOutputMirror(os.Stdout)
	writer.SetStyle(table.StyleRounded)
	writer.SetTitle("Dedup FinOps Summary")
	writer.AppendHeader(table.Row{"Metric", "Count", "Volume"})
	writer.AppendRow(table.Row{"Total processed", snap.TotalProcessedCount, formatBytes(snap.TotalProcessedBytes)})
	writer.AppendRow(table.Row{"Uploaded as unique", snap.UploadedUniqueCount, formatBytes(snap.UploadedUniqueBytes)})
	writer.AppendRow(table.Row{"Duplicates caught by full hash", snap.HashChunkDuplicateCount, formatBytes(snap.HashChunkSavedBytes) + " saved"})
	writer.AppendRow(table.Row{"Duplicates caught by AI/semantic", snap.SemanticDuplicateCount, formatBytes(snap.SemanticSavedBytes) + " saved"})
	writer.AppendRow(table.Row{"Replaced with better quality", snap.ReplacedCount, formatBytes(snap.ReplacedDeletedBytes) + " deleted"})
	writer.AppendRow(table.Row{"Failed to process", snap.FailedCount, formatBytes(snap.FailedBytes)})
	writer.AppendSeparator()
	writer.AppendRow(table.Row{"Not uploaded this run", "", fmt.Sprintf("%s (%.2f%%)", formatBytes(snap.BytesNotUploaded), snap.BytesNotUploadedPercent)})
	writer.AppendRow(table.Row{"Removed from the bucket", "", formatBytes(snap.BytesRemovedFromBucket)})
	writer.AppendRow(table.Row{"AI bypass rate", "", fmt.Sprintf("%.2f%%", snap.AIBypassRatePercent)})
	writer.AppendRow(table.Row{"Elapsed", "", fmt.Sprintf("%.3fs", snap.ElapsedSeconds)})
	writer.AppendRow(table.Row{"Throughput", fmt.Sprintf("%.2f img/s", snap.ImagesPerSecond), fmt.Sprintf("%.2f MB/s", snap.MegabytesPerSecond)})
	writer.AppendRow(table.Row{"Estimated monthly S3 savings", "", fmt.Sprintf("$%.4f", snap.EstimatedMonthlyUSD)})
	writer.Render()
}

func formatBytes(n int64) string {
	units := []string{"B", "KiB", "MiB", "GiB", "TiB"}
	value := float64(n)
	unit := 0
	for value >= 1024 && unit < len(units)-1 {
		value /= 1024
		unit++
	}
	if unit == 0 {
		return fmt.Sprintf("%d %s", n, units[unit])
	}
	return fmt.Sprintf("%.2f %s", value, units[unit])
}
