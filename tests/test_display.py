"""Tests for monitor models, resolution signatures and group selection helpers."""

from __future__ import annotations

import unittest

from screenpainter.core.display import (
    DisplayInfo,
    MonitorInfo,
    ResolutionError,
    format_resolution,
    normalize_resolution,
    resolve_signature,
    window_signature,
)
from screenpainter.core.geometry import Rect


def _monitor(index: int, x: int, y: int, width: int, height: int, *, primary: bool = False, dpi: int = 96) -> MonitorInfo:
    bounds = Rect(x, y, width, height)
    return MonitorInfo(
        index=index,
        device=f"\\\\.\\DISPLAY{index + 1}",
        bounds=bounds,
        work_area=bounds,
        primary=primary,
        dpi=dpi,
    )


class ResolutionTests(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(format_resolution(1920, 1080), "1920x1080")

    def test_normalize(self) -> None:
        self.assertEqual(normalize_resolution("1920x1080"), "1920x1080")
        self.assertEqual(normalize_resolution(" 1920 X 1080 "), "1920x1080")
        self.assertEqual(normalize_resolution("1920 × 1080"), "1920x1080")
        self.assertEqual(normalize_resolution("800x600"), "800x600")

    def test_invalid(self) -> None:
        for value in ("", "wide", "1920", "1920x", "x1080", "0x1080", "1920x-1", 123):
            with self.subTest(value=value), self.assertRaises(ResolutionError):
                normalize_resolution(value)  # type: ignore[arg-type]


class MonitorInfoTests(unittest.TestCase):
    def test_resolution_and_label(self) -> None:
        monitor = _monitor(0, 0, 0, 1920, 1080, primary=True, dpi=144)
        self.assertEqual(monitor.resolution, "1920x1080")
        self.assertIn("primary", monitor.label)
        self.assertIn("144", monitor.label)
        self.assertIn("DISPLAY1", monitor.label)

    def test_intersection_area(self) -> None:
        monitor = _monitor(0, 0, 0, 100, 100)
        self.assertEqual(monitor.intersection_area(Rect(50, 50, 100, 100)), 2500)
        self.assertEqual(monitor.intersection_area(Rect(200, 200, 10, 10)), 0)


class DisplayInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.primary = _monitor(0, 0, 0, 1920, 1080, primary=True)
        self.second = _monitor(1, 1920, 0, 2560, 1440)
        self.displays = DisplayInfo(
            monitors=(self.primary, self.second),
            virtual_bounds=Rect(0, 0, 4480, 1440),
        )

    def test_primary(self) -> None:
        self.assertIs(self.displays.primary, self.primary)
        self.assertIs(DisplayInfo().primary, None)

    def test_fallback_primary_when_no_primary_flag(self) -> None:
        displays = DisplayInfo(monitors=(self.second,))
        self.assertIs(displays.primary, self.second)

    def test_by_index(self) -> None:
        self.assertIs(self.displays.by_index(1), self.second)
        self.assertIsNone(self.displays.by_index(9))

    def test_monitor_for_uses_largest_overlap(self) -> None:
        self.assertIs(self.displays.monitor_for(Rect(100, 100, 200, 200)), self.primary)
        self.assertIs(self.displays.monitor_for(Rect(2000, 100, 200, 200)), self.second)
        # Straddling the seam: 90% on the second display.
        self.assertIs(self.displays.monitor_for(Rect(1900, 100, 200, 200)), self.second)

    def test_monitor_for_off_screen_falls_back_to_primary(self) -> None:
        self.assertIs(self.displays.monitor_for(Rect(-5000, -5000, 10, 10)), self.primary)

    def test_signature_for(self) -> None:
        self.assertEqual(self.displays.signature_for(Rect(10, 10, 100, 100)), "1920x1080")
        self.assertEqual(self.displays.signature_for(Rect(2000, 10, 100, 100)), "2560x1440")

    def test_signature_for_without_monitors(self) -> None:
        self.assertEqual(DisplayInfo().signature_for(Rect(0, 0, 10, 10)), "")


class SignatureResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.displays = DisplayInfo(
            monitors=(_monitor(0, 0, 0, 1920, 1080, primary=True),),
            virtual_bounds=Rect(0, 0, 1920, 1080),
        )

    def test_window_signature(self) -> None:
        self.assertEqual(window_signature(Rect(0, 0, 1121, 685)), "1121x685")

    def test_monitor_match(self) -> None:
        self.assertEqual(
            resolve_signature("monitor", Rect(100, 100, 800, 600), self.displays),
            "1920x1080",
        )

    def test_window_match(self) -> None:
        self.assertEqual(
            resolve_signature("window", Rect(100, 100, 800, 600), self.displays),
            "800x600",
        )

    def test_missing_bounds(self) -> None:
        self.assertEqual(resolve_signature("monitor", None, self.displays), "")
        self.assertEqual(resolve_signature("window", None, self.displays), "")


if __name__ == "__main__":
    unittest.main()
