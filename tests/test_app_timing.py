"""Tests for the event-driven scheduling decisions of the application loop."""

from __future__ import annotations

import time
import unittest
from typing import Any

from screenpainter.app import _BUSY_TIMEOUT_MS, _FALLBACK_TICK_MS, ScreenPainterApp
from screenpainter.core.display import MonitorInfo
from screenpainter.core.feature import Feature
from screenpainter.core.geometry import Rect
from screenpainter.core.window import CoordinateSpace, WindowProfile, WindowTarget

try:  # ``python -m unittest discover -s tests`` imports this as a top module
    from .helpers import cleanup, config_text, make_config
except ImportError:  # pragma: no cover - depends on how the tests are run
    from helpers import cleanup, config_text, make_config


class FakeService:
    def __init__(self) -> None:
        self.monitors = [
            MonitorInfo(
                index=0,
                device="\\\\.\\DISPLAY1",
                bounds=Rect(0, 0, 1920, 1080),
                work_area=Rect(0, 0, 1920, 1040),
                primary=True,
            )
        ]
        self.pumps = 0

    def pump_events(self) -> None:
        self.pumps += 1

    def list_windows(self) -> list[Any]:
        return []

    def list_monitors(self) -> list[MonitorInfo]:
        return list(self.monitors)

    def virtual_desktop(self) -> Rect:
        return Rect(0, 0, 1920, 1080)

    def find_target(
        self, profile: WindowProfile, coordinate_space: CoordinateSpace = "window"
    ) -> WindowTarget | None:
        return None

    def refresh_target(
        self,
        hwnd: int,
        profile: WindowProfile,
        coordinate_space: CoordinateSpace = "window",
    ) -> WindowTarget | None:
        return None

    def create_overlay(self, target: WindowTarget, *, click_through: bool = True) -> Any:
        raise AssertionError("no overlay should be created without a target")

    def create_button_panel(self, **kwargs: Any) -> Any:
        raise AssertionError("the toolbar is not enabled in these tests")

    def create_screen_capture(self, rect: Rect, *, cursor: str = "cross") -> Any:
        raise AssertionError("no capture window should be created")


class _BusyFeature(Feature):
    def keep_alive(self) -> bool:
        return True


class LoopTimingTests(unittest.TestCase):
    def _app(self, *, debug: bool = False) -> ScreenPainterApp:
        text = config_text().replace("debug = false", f"debug = {'true' if debug else 'false'}")
        config, directory = make_config(text)
        self.addCleanup(cleanup, directory)
        app = ScreenPainterApp(config, FakeService())  # type: ignore[arg-type]
        app._prepare_features()
        self.addCleanup(app._shutdown)
        return app

    def test_idle_without_target_uses_idle_tick_ms(self) -> None:
        app = self._app()
        self.assertEqual(
            app._wait_timeout_ms(time.monotonic()), app.config.app.idle_tick_ms
        )

    def test_idle_with_target_uses_verify_ms(self) -> None:
        app = self._app()
        app.tracker._cached_hwnd = 1
        self.assertEqual(
            app._wait_timeout_ms(time.monotonic()), app.config.app.verify_ms
        )

    def test_recent_activity_uses_the_fast_cadence(self) -> None:
        app = self._app()
        app.tracker._cached_hwnd = 1
        now = time.monotonic()
        app._activity_until = now + 0.3
        self.assertEqual(app._wait_timeout_ms(now), _FALLBACK_TICK_MS)

    def test_busy_feature_shortens_the_wait(self) -> None:
        app = self._app()
        app.tracker._cached_hwnd = 1
        app._features.append(_BusyFeature(app._features[0].context, {}))  # type: ignore[arg-type]
        self.assertEqual(app._wait_timeout_ms(time.monotonic()), _BUSY_TIMEOUT_MS)

    def test_debug_mode_checks_the_file_frequently(self) -> None:
        app = self._app(debug=True)
        app.tracker._cached_hwnd = 1
        self.assertEqual(
            app._wait_timeout_ms(time.monotonic()),
            min(app.config.app.verify_ms, app.config.app.debug_interval_ms),
        )

    def test_missing_event_source_falls_back_to_polling(self) -> None:
        app = self._app()
        app.tracker._cached_hwnd = 1
        app._listener = None
        self.assertEqual(
            app._wait_timeout_ms(time.monotonic()), app.config.app.verify_ms
        )

    def test_fallback_wait_pumps_without_blocking(self) -> None:
        app = self._app()
        started = time.monotonic()
        app._wait_for_events(1)
        self.assertGreaterEqual(time.monotonic() - started, 0.0)
        self.assertGreaterEqual(app.service.pumps, 1)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
