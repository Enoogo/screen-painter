"""Display (monitor) models and resolution signatures.

Rectangles are stored in named "groups", and every group declares the
``resolution`` it was designed for.  Resolution matching gives the user a way to
keep separate rectangle layouts for different display resolutions (or different
target window sizes) and switch between them.

This module is deliberately platform neutral: enumerating monitors is the
Windows backend's job (``platform/windows/service.py``).  Everything here is
pure logic so it can be unit tested without a desktop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .geometry import Rect

ResolutionMode = Literal["auto", "manual"]
ResolutionMatch = Literal["monitor", "window"]

RESOLUTION_MODES: frozenset[str] = frozenset({"auto", "manual"})
RESOLUTION_MATCHES: frozenset[str] = frozenset({"monitor", "window"})

_RESOLUTION_RE = re.compile(
    r"^(?P<width>\d{1,5})\s*[xX×*]\s*(?P<height>\d{1,5})$"
)


class ResolutionError(ValueError):
    """A resolution signature is not of the form ``<width>x<height>``."""


def format_resolution(width: int, height: int) -> str:
    """Return the canonical signature used in the configuration."""
    return f"{int(width)}x{int(height)}"


def normalize_resolution(value: str) -> str:
    """Validate and canonicalise ``"1920x1080"`` style signatures.

    The canonical form always uses a lowercase ``x`` and no spaces, so
    ``"1920 x 1080"`` and ``"1920X1080"`` both become ``"1920x1080"``.
    """
    if not isinstance(value, str):
        raise ResolutionError(f"resolution must be a string like '1920x1080' (got {value!r})")
    match = _RESOLUTION_RE.match(value.strip())
    if match is None:
        raise ResolutionError(
            f"resolution must look like '<width>x<height>', got {value!r}"
        )
    width = int(match.group("width"))
    height = int(match.group("height"))
    if width <= 0 or height <= 0:
        raise ResolutionError(f"resolution values must be positive, got {value!r}")
    return format_resolution(width, height)


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    """One physical display as reported by the platform layer."""

    index: int
    device: str
    bounds: Rect
    work_area: Rect
    primary: bool = False
    dpi: int = 96

    @property
    def resolution(self) -> str:
        return format_resolution(self.bounds.width, self.bounds.height)

    @property
    def label(self) -> str:
        flags = " (primary)" if self.primary else ""
        return (
            f"[{self.index}] {self.device} {self.resolution}{flags} "
            f"at {self.bounds.x},{self.bounds.y} dpi={self.dpi}"
        )

    def intersection_area(self, rect: Rect) -> int:
        overlap = self.bounds.intersection(rect)
        return overlap.area if overlap is not None else 0


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    """The current multi-monitor desktop."""

    monitors: tuple[MonitorInfo, ...] = ()
    virtual_bounds: Rect | None = None

    @property
    def primary(self) -> MonitorInfo | None:
        for monitor in self.monitors:
            if monitor.primary:
                return monitor
        return self.monitors[0] if self.monitors else None

    def by_index(self, index: int) -> MonitorInfo | None:
        for monitor in self.monitors:
            if monitor.index == index:
                return monitor
        return None

    def monitor_for(self, rect: Rect) -> MonitorInfo | None:
        """Return the monitor that shows most of ``rect``.

        A window can straddle two displays; picking the largest overlap matches
        what the user sees.  When the rectangle is completely off-screen the
        primary monitor is returned.
        """
        best: MonitorInfo | None = None
        best_area = 0
        for monitor in self.monitors:
            area = monitor.intersection_area(rect)
            if area > best_area:
                best = monitor
                best_area = area
        if best is not None:
            return best
        return self.primary

    def signature_for(self, rect: Rect) -> str:
        monitor = self.monitor_for(rect)
        if monitor is None:
            return ""
        return monitor.resolution


def window_signature(rect: Rect) -> str:
    """Signature of a target's overlay area (``overlay.coordinate_space``)."""
    return format_resolution(rect.width, rect.height)


def resolve_signature(
    match: ResolutionMatch,
    bounds: Rect | None,
    displays: DisplayInfo,
) -> str:
    """Compute the signature used to auto-select a rectangle group.

    ``match = "monitor"`` uses the display containing the target window (the
    common case: the mask was tuned for one screen resolution).
    ``match = "window"`` uses the target window's own size, which is useful when
    the same window layout must be masked on different monitors.
    """
    if bounds is None:
        return ""
    if match == "window":
        return window_signature(bounds)
    return displays.signature_for(bounds)
