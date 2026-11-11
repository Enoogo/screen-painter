"""Tests for the software pixel surface and colour conversion."""

from __future__ import annotations

import unittest

from screenpainter.core.surface import Color, PixelSurface


class ColorTests(unittest.TestCase):
    def test_hex_parsing(self) -> None:
        self.assertEqual(Color.from_hex("#102030"), Color(16, 32, 48, 255))
        self.assertEqual(Color.from_hex("#ffffff", 0.5), Color(255, 255, 255, 128))

    def test_invalid_hex(self) -> None:
        for value in ("", "#fff", "ffffff", "#gggggg", "#1234567"):
            with self.assertRaises(ValueError):
                Color.from_hex(value)

    def test_opacity_validation(self) -> None:
        for opacity in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                Color.from_hex("#000000", opacity)

    def test_premultiplied_bgra(self) -> None:
        self.assertEqual(Color(0, 0, 0, 255).premultiplied_bgra(), (0, 0, 0, 255))
        self.assertEqual(Color(255, 255, 255, 128).premultiplied_bgra(), (128, 128, 128, 128))
        self.assertEqual(Color(255, 0, 0, 0).premultiplied_bgra(), (0, 0, 0, 0))


class PixelSurfaceTests(unittest.TestCase):
    def test_initial_buffer_is_transparent(self) -> None:
        surface = PixelSurface(4, 3)
        self.assertEqual(len(surface.buffer), 4 * 3 * 4)
        self.assertEqual(surface.buffer, bytearray(4 * 3 * 4))

    def test_fill_rect_top_down_bgra(self) -> None:
        surface = PixelSurface(4, 3)
        surface.fill_rect(1, 1, 2, 1, Color(1, 2, 3, 255))
        expected_offset = (1 * 4 + 1) * 4
        self.assertEqual(
            surface.buffer[expected_offset : expected_offset + 4],
            bytes((3, 2, 1, 255)),
        )
        self.assertEqual(surface.buffer[0:4], bytes((0, 0, 0, 0)))

    def test_clipping_negative_and_overflowing(self) -> None:
        surface = PixelSurface(5, 5)
        surface.fill_rect(-3, -3, 5, 5, Color(0, 0, 0, 255))
        self.assertEqual(surface.buffer[0:4], bytes((0, 0, 0, 255)))
        self.assertEqual(
            surface.buffer[(1 * 5 + 1) * 4 : (1 * 5 + 1) * 4 + 4],
            bytes((0, 0, 0, 255)),
        )
        self.assertEqual(
            surface.buffer[(2 * 5 + 2) * 4 : (2 * 5 + 2) * 4 + 4],
            bytes(4),
        )
        surface.clear()
        surface.fill_rect(4, 4, 100, 100, Color(0, 0, 0, 255))
        self.assertEqual(surface.buffer[-4:], bytes((0, 0, 0, 255)))

    def test_invalid_dimensions_and_clipping(self) -> None:
        with self.assertRaises(ValueError):
            PixelSurface(0, 10)
        surface = PixelSurface(2, 2)
        surface.fill_rect(-10, -10, 2, 2, Color(0, 0, 0, 255))
        self.assertEqual(surface.buffer, bytearray(16))

    def test_transparent_fill_is_noop(self) -> None:
        surface = PixelSurface(2, 2)
        surface.fill_rect(0, 0, 2, 2, Color(255, 255, 255, 0))
        self.assertEqual(surface.buffer, bytearray(16))


if __name__ == "__main__":
    unittest.main()
