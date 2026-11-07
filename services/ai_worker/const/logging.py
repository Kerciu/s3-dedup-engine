"""Logger naming, Rich console sizing and the environment flags that tune them."""

from typing import Final, Tuple

LOGGER_NAME: Final[str] = "ai_worker"
LOG_FORMAT: Final[str] = "%(message)s"
LOG_TIME_FORMAT: Final[str] = "[%X]"
LOG_DEFAULT_LEVEL: Final[str] = "INFO"
LOG_LEVEL_ENV: Final[str] = "LOG_LEVEL"
LOG_FORCE_COLOR_ENV: Final[str] = "LOG_FORCE_COLOR"
LOG_TRACEBACK_LOCALS_ENV: Final[str] = "LOG_TRACEBACK_LOCALS"
LOG_QUIET_LEVEL: Final[str] = "WARNING"
LOG_NON_TTY_WIDTH: Final[int] = 200
LOG_NON_TTY_HEIGHT: Final[int] = 50
LOG_QUIET_LOGGERS: Final[Tuple[str, ...]] = (
    "filelock",
    "httpcore",
    "httpx",
    "httpx2",
    "urllib3",
)
LOG_TRUTHY_VALUES: Final[Tuple[str, ...]] = ("1", "true", "yes", "on")
