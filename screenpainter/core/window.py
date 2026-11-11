"""Window descriptions shared by the platform layer and features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Pattern

from .geometry import Rect

SelectStrategy = Literal["largest", "first"]
CoordinateSpace = Literal["window", "client"]


@dataclass(frozen=True, slots=True)
class WindowProfile:
    """A configurable rule for finding a target window.

    Within one profile all non-empty criteria groups must match (AND).  Values
    inside one group are alternatives (OR).  This keeps common profiles simple
    while still allowing process + title + class combinations.
    """

    name: str
    description: str = ""
    process_names: tuple[str, ...] = ()
    title_patterns: tuple[Pattern[str], ...] = ()
    class_names: tuple[str, ...] = ()
    exclude_title_patterns: tuple[Pattern[str], ...] = ()
    min_width: int = 0
    min_height: int = 0
    select: SelectStrategy = "largest"

    def matches_identity(
        self,
        *,
        title: str,
        class_name: str,
        process_name: str | None,
    ) -> bool:
        """Check name-based criteria (not dimensions)."""
        if self.process_names:
            if process_name is None:
                return False
            wanted = {item.casefold() for item in self.process_names}
            if process_name.casefold() not in wanted:
                return False

        if self.class_names:
            if class_name not in self.class_names:
                return False

        if self.title_patterns:
            if not any(pattern.search(title) for pattern in self.title_patterns):
                return False

        if self.exclude_title_patterns and any(
            pattern.search(title) for pattern in self.exclude_title_patterns
        ):
            return False

        return True

    def matches_size(self, width: int, height: int) -> bool:
        return width >= self.min_width and height >= self.min_height

    def matches(
        self,
        *,
        title: str,
        class_name: str,
        process_name: str | None,
        width: int,
        height: int,
    ) -> bool:
        return self.matches_identity(
            title=title, class_name=class_name, process_name=process_name
        ) and self.matches_size(width, height)

    def describe(self) -> str:
        fields: list[str] = []
        if self.process_names:
            fields.append("process=" + ",".join(self.process_names))
        if self.class_names:
            fields.append("class=" + ",".join(self.class_names))
        if self.title_patterns:
            fields.append("title=" + ",".join(pattern.pattern for pattern in self.title_patterns))
        return " ".join(fields) if fields else "<empty profile>"


@dataclass(frozen=True, slots=True)
class WindowTarget:
    """A concrete, currently existing native window.

    ``bounds`` is the rectangle the overlay covers, in physical screen
    coordinates.  It equals either ``window_bounds`` or ``client_bounds``
    depending on ``overlay.coordinate_space`` in the configuration.
    """

    hwnd: int
    pid: int
    process_name: str | None
    executable: str | None
    title: str
    class_name: str
    bounds: Rect
    window_bounds: Rect
    client_bounds: Rect
    minimized: bool = False
    foreground: bool = False
    coordinate_space: CoordinateSpace = "window"

    @property
    def size(self) -> tuple[int, int]:
        return (self.bounds.width, self.bounds.height)

    @property
    def identity(self) -> tuple[int, int]:
        return (self.hwnd, self.pid)

    def summary(self) -> str:
        process = self.process_name or "<unknown-process>"
        title = self.title or "<untitled>"
        return (
            f"hwnd=0x{self.hwnd:X} pid={self.pid} process={process} "
            f"class={self.class_name} title={title!r} bounds={self.bounds}"
        )


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """Diagnostic description of one visible top-level window."""

    hwnd: int
    pid: int
    process_name: str | None
    executable: str | None = field(default=None, repr=False)
    title: str = ""
    class_name: str = ""
    window_bounds: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    client_bounds: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    minimized: bool = False
    cloaked: bool = False

    def matches(self, profile: WindowProfile) -> bool:
        return profile.matches(
            title=self.title,
            class_name=self.class_name,
            process_name=self.process_name,
            width=self.window_bounds.width,
            height=self.window_bounds.height,
        )
