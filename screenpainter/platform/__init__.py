"""Platform factory.  Only Windows is implemented today."""

from __future__ import annotations

import sys

from screenpainter.errors import PlatformError

from .base import Overlay, WindowService


def create_window_service() -> WindowService:
    if sys.platform != "win32":
        raise PlatformError(
            "ScreenPainter currently has a Windows-only overlay backend. "
            "Add a backend under screenpainter/platform/ and register it here."
        )
    from .windows.service import WindowsWindowService

    return WindowsWindowService()


__all__ = ["Overlay", "WindowService", "create_window_service"]
