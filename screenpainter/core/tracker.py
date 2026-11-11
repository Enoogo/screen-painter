"""Stable target tracking built on a platform window service."""

from __future__ import annotations

from screenpainter.core.window import CoordinateSpace, WindowProfile, WindowTarget
from screenpainter.platform.base import WindowService


class TargetTracker:
    """Keeps one window handle selected until it disappears.

    Re-enumerating on every frame would be wasteful and could make the overlay
    jump between multiple matching windows.  We therefore verify the cached
    HWND inexpensively and only perform a full search when it is gone or no
    longer matches the profile.
    """

    def __init__(
        self,
        service: WindowService,
        profile: WindowProfile,
        *,
        coordinate_space: CoordinateSpace = "window",
    ) -> None:
        self.service = service
        self.profile = profile
        self.coordinate_space = coordinate_space
        self._cached_hwnd: int | None = None

    def tick(self) -> WindowTarget | None:
        if self._cached_hwnd is not None:
            target = self.service.refresh_target(
                self._cached_hwnd,
                self.profile,
                self.coordinate_space,
            )
            if target is not None:
                return target
            self._cached_hwnd = None

        target = self.service.find_target(self.profile, self.coordinate_space)
        if target is not None:
            self._cached_hwnd = target.hwnd
        return target

    def reset(self) -> None:
        self._cached_hwnd = None

    @property
    def cached_hwnd(self) -> int | None:
        return self._cached_hwnd
