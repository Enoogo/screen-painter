"""Small immutable geometry primitives used across the application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Size:
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle in integer screen (or surface) coordinates.

    ``x``/``y`` is the top-left corner, matching Win32 conventions.
    Negative positions are legal because windows can partially leave the
    desktop.
    """

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def translated(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def intersection(self, other: "Rect") -> "Rect | None":
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)

    def clamp_to(self, outer: "Rect") -> "Rect | None":
        """Return the part of this rectangle inside ``outer``."""
        return self.intersection(outer)

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> "Rect":
        return cls(int(left), int(top), max(0, int(right) - int(left)), max(0, int(bottom) - int(top)))

    def __str__(self) -> str:
        return f"{self.x},{self.y} {self.width}x{self.height}"
