"""Application specific exceptions with user-facing messages."""

from __future__ import annotations


class ScreenPainterError(Exception):
    """Base class for expected, actionable application errors."""


class ConfigError(ScreenPainterError):
    """The configuration file is missing, malformed or invalid."""


class ConfigWriteError(ScreenPainterError):
    """The configuration file could not be edited/persisted."""


class FeatureError(ScreenPainterError):
    """A feature package could not be loaded or configured."""


class PlatformError(ScreenPainterError):
    """The current platform or a Win32 call is not usable."""


class OverlayError(PlatformError):
    """The native overlay window could not be created or updated."""
