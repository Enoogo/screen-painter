"""Logging setup: console plus an optional rotating UTF-8 file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LoggingConfig

LOGGER_NAME = "screenpainter"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    settings: LoggingConfig,
    project_root: Path,
    *,
    level_override: str | None = None,
) -> logging.Logger:
    """Configure the application logger and return it.

    Repeated calls replace handlers, which makes test setup predictable.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logger.disabled = False

    level_name = (level_override or settings.level or "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    logger.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    if settings.console:
        console_handler = logging.StreamHandler(stream=sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if settings.file:
        log_path = Path(settings.file).expanduser()
        if not log_path.is_absolute():
            log_path = project_root / log_path
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=settings.max_bytes,
                backupCount=settings.backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("file logging disabled; cannot open %s: %s", log_path, exc)

    if not logger.handlers:
        # Never leave the app with no handler at all (for example if both
        # console and file logging are disabled).
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    suffix = f".{name}" if name else ""
    return logging.getLogger(f"{LOGGER_NAME}{suffix}")
