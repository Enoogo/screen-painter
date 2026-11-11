"""Tests for the cached target tracker."""

from __future__ import annotations

import re
import unittest

from screenpainter.core.geometry import Rect
from screenpainter.core.tracker import TargetTracker
from screenpainter.core.window import WindowProfile, WindowTarget


def _profile() -> WindowProfile:
    return WindowProfile(name="demo", title_patterns=(re.compile("^Demo"),))


def _target(hwnd: int = 10) -> WindowTarget:
    return WindowTarget(
        hwnd=hwnd,
        pid=20,
        process_name="demo.exe",
        executable="C:/demo.exe",
        title="Demo",
        class_name="DemoWindow",
        bounds=Rect(0, 0, 100, 100),
        window_bounds=Rect(0, 0, 100, 100),
        client_bounds=Rect(0, 0, 100, 100),
    )


class FakeService:
    def __init__(self) -> None:
        self.find_result: WindowTarget | None = None
        self.refresh_results: list[WindowTarget | None] = []
        self.find_calls = 0
        self.refresh_calls: list[int] = []

    def pump_events(self) -> None:
        pass

    def list_windows(self):  # pragma: no cover - protocol completeness
        return []

    def find_target(self, profile, coordinate_space="window"):
        self.find_calls += 1
        return self.find_result

    def refresh_target(self, hwnd, profile, coordinate_space="window"):
        self.refresh_calls.append(hwnd)
        if self.refresh_results:
            return self.refresh_results.pop(0)
        return None

    def create_overlay(self, target, *, click_through=True):  # pragma: no cover
        raise NotImplementedError


class TargetTrackerTests(unittest.TestCase):
    def test_caches_handle_and_reuses_refresh(self) -> None:
        service = FakeService()
        service.find_result = _target(10)
        tracker = TargetTracker(service, _profile())
        first = tracker.tick()
        self.assertEqual(first.hwnd, 10)
        self.assertEqual(service.find_calls, 1)

        service.refresh_results = [_target(10)]
        second = tracker.tick()
        self.assertEqual(second.hwnd, 10)
        self.assertEqual(service.refresh_calls, [10])
        self.assertEqual(service.find_calls, 1)

    def test_rediscovery_when_cached_window_disappears(self) -> None:
        service = FakeService()
        service.find_result = _target(10)
        tracker = TargetTracker(service, _profile())
        tracker.tick()

        service.refresh_results = [None]
        service.find_result = _target(11)
        target = tracker.tick()
        self.assertEqual(target.hwnd, 11)
        self.assertEqual(service.refresh_calls, [10])
        self.assertEqual(service.find_calls, 2)
        self.assertEqual(tracker.cached_hwnd, 11)

    def test_no_target_retries_find(self) -> None:
        service = FakeService()
        service.find_result = None
        tracker = TargetTracker(service, _profile())
        self.assertIsNone(tracker.tick())
        self.assertIsNone(tracker.cached_hwnd)
        self.assertIsNone(tracker.tick())
        self.assertEqual(service.find_calls, 2)

    def test_reset_forces_enumeration(self) -> None:
        service = FakeService()
        service.find_result = _target(10)
        tracker = TargetTracker(service, _profile())
        tracker.tick()
        tracker.reset()
        tracker.tick()
        self.assertEqual(service.find_calls, 2)


if __name__ == "__main__":
    unittest.main()
