"""Tests for the Rich logging setup."""

from __future__ import annotations

import logging

import pytest
from rich.logging import RichHandler

from const.log import (
    LOG_DEFAULT_LEVEL,
    LOG_FORCE_COLOR_ENV,
    LOG_LEVEL_ENV,
    LOG_NON_TTY_HEIGHT,
    LOG_NON_TTY_WIDTH,
    LOG_QUIET_LEVEL,
    LOG_QUIET_LOGGERS,
    LOG_TRACEBACK_LOCALS_ENV,
    LOGGER_NAME,
)
from logger import (
    build_handler,
    console_size,
    env_flag,
    forced_color,
    get_logger,
    quiet_third_party,
    resolve_level,
    setup_logging,
)


def test_get_logger_uses_the_shared_name() -> None:
    assert get_logger().name == LOGGER_NAME


def test_resolve_level_prefers_the_explicit_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_LEVEL_ENV, "ERROR")
    assert resolve_level("debug") == "DEBUG"


def test_resolve_level_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_LEVEL_ENV, " warning ")
    assert resolve_level() == "WARNING"


def test_resolve_level_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    assert resolve_level() == LOG_DEFAULT_LEVEL


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_accepts_affirmative_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SOME_FLAG", value)
    assert env_flag("SOME_FLAG")


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_env_flag_rejects_everything_else(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SOME_FLAG", value)
    assert not env_flag("SOME_FLAG")


def test_env_flag_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert not env_flag("SOME_FLAG")


def test_forced_color_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOG_FORCE_COLOR_ENV, raising=False)
    assert forced_color() is None


def test_forced_color_returns_true_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_FORCE_COLOR_ENV, "1")
    assert forced_color() is True


def test_handler_disables_markup_so_filenames_cannot_inject_tags() -> None:
    assert build_handler().markup is False


def test_handler_enables_rich_tracebacks() -> None:
    assert build_handler().rich_tracebacks is True


def test_handler_hides_locals_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOG_TRACEBACK_LOCALS_ENV, raising=False)
    assert build_handler().tracebacks_show_locals is False


def test_handler_shows_locals_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_TRACEBACK_LOCALS_ENV, "1")
    assert build_handler().tracebacks_show_locals is True


def test_handler_writes_to_stderr() -> None:
    assert build_handler().console.stderr is True


def test_console_size_is_pinned_off_tty() -> None:
    assert console_size() == (LOG_NON_TTY_WIDTH, LOG_NON_TTY_HEIGHT)


def test_handler_console_is_wide_off_tty() -> None:
    assert build_handler().console.width >= LOG_NON_TTY_WIDTH - 1


def test_long_log_lines_are_not_wrapped(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging("INFO")
    get_logger().info(
        "dedup decision key=%s status=%s distance=%.4f existing=%s",
        "a-long-image-name-for-an-ecommerce-product-photo.jpg",
        "duplicate_rejected",
        0.0223,
        "incumbent-product-photo.jpg",
    )
    rendered = capsys.readouterr().err
    assert "status=duplicate_rejected distance=0.0223" in rendered


def test_setup_logging_installs_a_single_rich_handler(
    restore_logging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    setup_logging()
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], RichHandler)


def test_setup_logging_applies_the_requested_level(restore_logging: None) -> None:
    setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_is_idempotent(restore_logging: None) -> None:
    setup_logging("INFO")
    setup_logging("INFO")
    assert len(logging.getLogger().handlers) == 1


def test_setup_logging_returns_the_app_logger(restore_logging: None) -> None:
    assert setup_logging("INFO").name == LOGGER_NAME


def test_quiet_third_party_raises_noisy_thresholds() -> None:
    quiet_third_party()
    expected = logging.getLevelName(LOG_QUIET_LEVEL)
    for name in LOG_QUIET_LOGGERS:
        assert logging.getLogger(name).level == expected


def test_messages_render_through_rich_to_stderr(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging("INFO")
    get_logger().info("quality key=%s total=%.4f", "photo.jpg", 0.9161)
    rendered = capsys.readouterr().err
    assert "quality key=photo.jpg total=0.9161" in rendered
    assert "INFO" in rendered


def test_debug_is_suppressed_at_info_level(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging("INFO")
    get_logger().debug("nearest neighbor for %s", "photo.jpg")
    assert "nearest neighbor" not in capsys.readouterr().err


def test_debug_is_emitted_at_debug_level(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging("DEBUG")
    get_logger().debug("nearest neighbor for %s", "photo.jpg")
    assert "nearest neighbor" in capsys.readouterr().err


def test_bracketed_filenames_are_not_consumed_as_markup(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging("INFO")
    get_logger().info("received image stream key=%s", "photo[1].jpg")
    assert "photo[1].jpg" in capsys.readouterr().err
