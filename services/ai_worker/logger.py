"""Rich-backed logging setup shared by every AI worker entry point."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Tuple

from rich.console import Console
from rich.logging import RichHandler

from const.log import (
    LOG_DEFAULT_LEVEL,
    LOG_FORCE_COLOR_ENV,
    LOG_FORMAT,
    LOG_LEVEL_ENV,
    LOG_NON_TTY_HEIGHT,
    LOG_NON_TTY_WIDTH,
    LOG_QUIET_LEVEL,
    LOG_QUIET_LOGGERS,
    LOG_TIME_FORMAT,
    LOG_TRACEBACK_LOCALS_ENV,
    LOG_TRUTHY_VALUES,
    LOGGER_NAME,
)


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Installs a RichHandler as the only root handler and returns the app logger."""
    logging.basicConfig(
        level=resolve_level(level),
        format=LOG_FORMAT,
        datefmt=LOG_TIME_FORMAT,
        handlers=[build_handler()],
        force=True,
    )
    quiet_third_party()
    return get_logger()


def get_logger() -> logging.Logger:
    """Returns the shared AI worker logger."""
    return logging.getLogger(LOGGER_NAME)


def resolve_level(level: Optional[str] = None) -> str:
    """Resolves the log level from the argument, the environment, or the default."""
    candidate = level or os.getenv(LOG_LEVEL_ENV) or LOG_DEFAULT_LEVEL
    return candidate.strip().upper()


def build_handler() -> RichHandler:
    """Builds a RichHandler with colored levels, highlighting and rich tracebacks."""
    width, height = console_size()
    return RichHandler(
        console=Console(
            stderr=True,
            force_terminal=forced_color(),
            width=width,
            height=height,
        ),
        rich_tracebacks=True,
        tracebacks_show_locals=env_flag(LOG_TRACEBACK_LOCALS_ENV),
        log_time_format=LOG_TIME_FORMAT,
        markup=False,
        show_path=True,
    )


def quiet_third_party() -> None:
    """Raises the threshold of chatty dependency loggers to keep output readable."""
    for name in LOG_QUIET_LOGGERS:
        logging.getLogger(name).setLevel(LOG_QUIET_LEVEL)


def console_size() -> Tuple[Optional[int], Optional[int]]:
    """Returns a fixed size off-TTY so structured log lines are never wrapped."""
    stream = sys.stderr
    if hasattr(stream, "isatty") and stream.isatty():
        return None, None
    return LOG_NON_TTY_WIDTH, LOG_NON_TTY_HEIGHT


def forced_color() -> Optional[bool]:
    """Returns True to force color, or None to leave Rich autodetection in charge."""
    return True if env_flag(LOG_FORCE_COLOR_ENV) else None


def env_flag(name: str) -> bool:
    """Returns True when the named environment variable holds an affirmative value."""
    return os.getenv(name, "").strip().lower() in LOG_TRUTHY_VALUES
