"""Tests for debug mode: configuration hot reload in the application loop."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from screenpainter.app import ScreenPainterApp
from screenpainter.core.display import MonitorInfo
from screenpainter.core.geometry import Rect
from screenpainter.core.window import CoordinateSpace, WindowProfile, WindowTarget

try:  # ``python -m unittest discover -s tests`` imports this as a top module
    from .helpers import cleanup, config_text, make_config
except ImportError:  # pragma: no cover - depends on how the tests are run
    from helpers import cleanup, config_text, make_config

DEBUG_LAYOUT = """\
[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = false
"""


class FakeService:
    """The minimal window service the app needs while nothing is on screen."""

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

    def pump_events(self) -> None:
        pass

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


class _TargetSpy:
    """Records the arguments of ``Feature.on_target_changed``."""

    def __init__(self) -> None:
        self.events: list[WindowTarget | None] = []

    def on_target_changed(self, target: WindowTarget | None) -> None:
        self.events.append(target)


class HotReloadTests(unittest.TestCase):
    def _app(self, text: str) -> tuple[ScreenPainterApp, Path, FakeService]:
        config, directory = make_config(text)
        self.addCleanup(cleanup, directory)
        service = FakeService()
        app = ScreenPainterApp(config, service)  # type: ignore[arg-type]
        app._prepare_features()
        self.addCleanup(app._shutdown)
        return app, config.path, service

    def _tick(self, app: ScreenPainterApp, offset: float) -> None:
        app._tick_config_watch(time.monotonic() + offset)

    def _config_with_debug(self, enabled: bool, *, extra_groups: str = "") -> str:
        text = config_text(layout=DEBUG_LAYOUT, groups=extra_groups)
        return text.replace(
            "debug = false", f"debug = {'true' if enabled else 'false'}"
        )

    def test_change_is_ignored_outside_debug_mode(self) -> None:
        app, path, _ = self._app(self._config_with_debug(False))
        original = app.config
        path.write_text(
            self._config_with_debug(False, extra_groups="""
[groups.extra]
resolution = "2560x1440"
rectangles = []
"""),
            encoding="utf-8",
        )
        self._tick(app, 0)
        self.assertIs(app.config, original)
        self.assertIsNone(app.config.find_group("extra"))

    def test_change_is_applied_in_debug_mode(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        self.assertIsNone(app.config.find_group("extra"))
        feature_before = app.features[0]

        path.write_text(
            self._config_with_debug(True, extra_groups="""
[groups.extra]
resolution = "2560x1440"
rectangles = []
"""),
            encoding="utf-8",
        )
        self._tick(app, 0)
        self.assertIsNotNone(app.config.find_group("extra"))
        self.assertIsNotNone(app.store.groups.get("extra"))
        # Unchanged features must survive a reload so their state (and windows)
        # are preserved.
        self.assertIs(app.features[0], feature_before)

    def test_invalid_change_is_rejected_and_keeps_running(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        original = app.config
        path.write_text("this is not toml [", encoding="utf-8")
        self._tick(app, 0)
        self.assertIs(app.config, original)
        # The same broken revision is reported only once.
        self.assertIsNotNone(app._rejected_stamp)

        path.write_text(
            self._config_with_debug(True, extra_groups="""
[groups.extra]
resolution = "2560x1440"
rectangles = []
"""),
            encoding="utf-8",
        )
        self._tick(app, 1.0)
        self.assertIsNotNone(app.config.find_group("extra"))

    def test_unknown_keys_are_rejected(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        original = app.config
        path.write_text(
            self._config_with_debug(True).replace("verify_ms = 1000", "verify_ms = 1000\nhot = true"),
            encoding="utf-8",
        )
        self._tick(app, 0)
        self.assertIs(app.config, original)

    def test_own_write_does_not_trigger_a_reload(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        original = app.config
        # Simulate the toolbar persisting something: the app knows about the
        # write and must not reload its own change.
        path.write_text(
            self._config_with_debug(True).replace("hidden = false", "hidden = true"),
            encoding="utf-8",
        )
        app._note_config_saved()
        self._tick(app, 0)
        self.assertIs(app.config, original)

    def test_disabling_a_feature_removes_it(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        self.assertEqual(len(app.features), 1)
        text = self._config_with_debug(True).replace(
            "enabled = true", "enabled = false"
        )
        path.write_text(text, encoding="utf-8")
        self._tick(app, 0)
        self.assertEqual(len(app.features), 0)

    def test_target_profile_change_resets_the_tracker(self) -> None:
        app, path, _ = self._app(self._config_with_debug(True))
        text = self._config_with_debug(True).replace(
            'process_names = ["demo.exe"]',
            'process_names = ["other.exe"]',
        )
        path.write_text(text, encoding="utf-8")
        self._tick(app, 0)
        self.assertEqual(app.tracker.profile.process_names, ("other.exe",))

    def test_unchanged_file_is_not_reloaded(self) -> None:
        app, _, _ = self._app(self._config_with_debug(True))
        original = app.config
        self._tick(app, 0)
        self.assertIs(app.config, original)

    def test_debug_override_enables_watching(self) -> None:
        app, path, _ = self._app(self._config_with_debug(False))
        app.debug_override = True
        self.assertIsNone(app.config.find_group("extra"))
        path.write_text(
            self._config_with_debug(False, extra_groups="""
[groups.extra]
resolution = "2560x1440"
rectangles = []
"""),
            encoding="utf-8",
        )
        self._tick(app, 0)
        self.assertIsNotNone(app.config.find_group("extra"))

    def test_resolution_signature_refresh_selects_group(self) -> None:
        app, _, _ = self._app(self._config_with_debug(True))
        self.assertEqual(app.store.signature, "")
        app._refresh_signature(
            WindowTarget(
                hwnd=1,
                pid=2,
                process_name="demo.exe",
                executable="C:/demo.exe",
                title="Demo",
                class_name="DemoWindow",
                bounds=Rect(10, 10, 800, 600),
                window_bounds=Rect(10, 10, 800, 600),
                client_bounds=Rect(10, 10, 800, 600),
            )
        )
        self.assertEqual(app.store.signature, "1920x1080")
        self.assertEqual(app.store.active_group_name, "demo")

    def test_minimizing_reports_the_target_as_unavailable(self) -> None:
        app, _, _ = self._app(self._config_with_debug(True))
        spy = _TargetSpy()
        app._features.append(spy)  # type: ignore[arg-type]
        target = WindowTarget(
            hwnd=1,
            pid=2,
            process_name="demo.exe",
            executable="C:/demo.exe",
            title="Demo",
            class_name="DemoWindow",
            bounds=Rect(10, 10, 800, 600),
            window_bounds=Rect(10, 10, 800, 600),
            client_bounds=Rect(10, 10, 800, 600),
        )
        app._notified_target_hwnd = target.hwnd
        # Hide the parts that would create a real native overlay.
        app._ensure_overlay = lambda *args, **kwargs: None  # type: ignore[method-assign]

        app._handle_target(target, 0.0)
        self.assertEqual(spy.events, [])
        app._handle_target(replace(target, minimized=True), 0.0)
        self.assertEqual(spy.events, [None])
        app._handle_target(replace(target, minimized=False), 0.0)
        self.assertEqual(spy.events, [None, target])


if __name__ == "__main__":
    unittest.main()
