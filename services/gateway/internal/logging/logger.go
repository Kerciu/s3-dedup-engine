package logging

import (
	"log/slog"
	"os"
	"time"

	"github.com/lmittmann/tint"
	"golang.org/x/term"
)

func Setup(verbose bool) *slog.Logger {
	level := slog.LevelInfo
	if verbose {
		level = slog.LevelDebug
	}

	handler := tint.NewHandler(os.Stderr, &tint.Options{
		Level:      level,
		TimeFormat: time.Kitchen,
		NoColor:    !term.IsTerminal(int(os.Stderr.Fd())),
	})
	logger := slog.New(handler)
	slog.SetDefault(logger)
	return logger
}
