package logging

import (
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/lmittmann/tint"
	"golang.org/x/term"

	"s3-dedup-engine/services/gateway/internal/constants"
)

func Setup(verbose bool) (string, func(), error) {
	logDir := filepath.Join(constants.DedupLogDir, time.Now().Format(constants.DedupLogTimeLayout))
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return "", nil, err
	}

	logFile, err := os.OpenFile(
		filepath.Join(logDir, constants.SavedLogFileName),
		os.O_CREATE|os.O_WRONLY|os.O_APPEND,
		0o644,
	)
	if err != nil {
		return "", nil, err
	}

	consoleLevel := slog.LevelInfo
	if verbose {
		consoleLevel = slog.LevelDebug
	}

	console := tint.NewHandler(os.Stderr, &tint.Options{
		Level:      consoleLevel,
		TimeFormat: time.Kitchen,
		NoColor:    !term.IsTerminal(int(os.Stderr.Fd())),
	})
	fileHandler := slog.NewTextHandler(logFile, &slog.HandlerOptions{Level: slog.LevelDebug})
	slog.SetDefault(slog.New(slog.NewMultiHandler(console, fileHandler)))

	cleanup := func() {
		_ = logFile.Close()
	}
	return logDir, cleanup, nil
}
