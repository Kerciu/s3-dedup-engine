"""Optional per-image JSONL metrics for the AI worker."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from const.grpc import AI_EVENTS_PATH_ENV
from logger import get_logger

log = get_logger()
_lock = threading.Lock()


def events_path() -> Optional[str]:
    """Returns the configured AI events path when writing is enabled."""
    path = os.getenv(AI_EVENTS_PATH_ENV, "").strip()
    return path or None


def append_event(event: dict[str, Any]) -> None:
    """Appends one image event when AI_EVENTS_PATH is set."""
    path = events_path()
    if path is None:
        return
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with _lock:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
        except OSError as exc:
            log.warning("could not append ai event: %s", exc)
