"""A tiny software canvas and colour helpers.

The surface stores pixels in Windows-native, top-down, **premultiplied BGRA**
order.  That is the exact layout required by ``UpdateLayeredWindow`` with
``AC_SRC_ALPHA``, so the overlay can upload the buffer without a per-pixel
conversion.

Features draw with simple primitives.  Rectangle drawing replaces the
destination pixels; for the current opaque black rectangles this is equivalent
to normal source-over compositing.  A future feature that needs true alpha
compositing can add a blending helper here without touching the application
loop.
"""

from __future__ import annotations

from dataclasses import dataclass

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _clamp_channel(value: int) -> int:
    return max(0, min(255, int(value)))


@dataclass(frozen=True, slots=True)
class Color:
    """An 8-bit sRGB colour with a separate opacity value."""

    r: int
    g: int
    b: int
    alpha: int = 255

    @classmethod
    def from_hex(cls, value: str, opacity: float = 1.0) -> "Color":
        if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
            raise ValueError(f"color must look like '#RRGGBB', got {value!r}")
        digits = value[1:]
        if any(ch not in _HEX_DIGITS for ch in digits):
            raise ValueError(f"color contains non-hex characters: {value!r}")
        if not (0.0 <= float(opacity) <= 1.0):
            raise ValueError("opacity must be between 0.0 and 1.0")
        return cls(
            r=int(digits[0:2], 16),
            g=int(digits[2:4], 16),
            b=int(digits[4:6], 16),
            alpha=round(255 * float(opacity)),
        )

    def premultiplied_bgra(self) -> tuple[int, int, int, int]:
        """Return the pixel bytes used by :class:`PixelSurface`."""
        a = _clamp_channel(self.alpha)
        if a == 0:
            return (0, 0, 0, 0)
        # Premultiplication is required when AC_SRC_ALPHA is used.
        b = (_clamp_channel(self.b) * a + 127) // 255
        g = (_clamp_channel(self.g) * a + 127) // 255
        r = (_clamp_channel(self.r) * a + 127) // 255
        return (b, g, r, a)


class PixelSurface:
    """A mutable top-down BGRA canvas.

    Coordinates are pixels relative to the overlay's top-left corner.
    """

    __slots__ = ("_width", "_height", "_buffer")

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"surface dimensions must be positive, got {width}x{height}")
        self._width = int(width)
        self._height = int(height)
        self._buffer = bytearray(self._width * self._height * 4)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def buffer(self) -> bytearray:
        """The raw premultiplied BGRA buffer (kept for the native compositor)."""
        return self._buffer

    def clear(self) -> None:
        self._buffer[:] = b"\x00" * len(self._buffer)

    def fill_all(self, color: Color) -> None:
        self.fill_rect(0, 0, self._width, self._height, color)

    def fill_rect(self, x: int, y: int, width: int, height: int, color: Color) -> None:
        """Fill a clipped rectangle with ``color`` (pixel-replacing)."""
        if width <= 0 or height <= 0 or color.alpha <= 0:
            return

        left = max(0, int(x))
        top = max(0, int(y))
        right = min(self._width, int(x) + int(width))
        bottom = min(self._height, int(y) + int(height))
        if right <= left or bottom <= top:
            return

        b, g, r, a = color.premultiplied_bgra()
        if a == 0:
            return
        pixel = bytes((b, g, r, a))
        row = pixel * (right - left)
        stride = self._width * 4
        start_offset = top * stride + left * 4
        for row_index in range(bottom - top):
            offset = start_offset + row_index * stride
            self._buffer[offset : offset + len(row)] = row
