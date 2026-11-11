"""Tests for the built-in rectangle painter and plugin discovery."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path

from screenpainter.core.feature import (
    FeatureContext,
    feature_class,
    registered_feature_types,
)
from screenpainter.core.geometry import Rect
from screenpainter.core.layout import LayoutStore, Shape
from screenpainter.core.surface import PixelSurface
from screenpainter.core.window import WindowTarget
from screenpainter.errors import FeatureError
from screenpainter.features import load_builtin_features
from screenpainter.features.black_rectangles import BlackRectanglesFeature

try:  # ``python -m unittest discover -s tests`` imports this as a top module
    from .helpers import cleanup, config_text, make_config
except ImportError:  # pragma: no cover - depends on how the tests are run
    from helpers import cleanup, config_text, make_config


def _target() -> WindowTarget:
    return WindowTarget(
        hwnd=1,
        pid=2,
        process_name="demo.exe",
        executable="C:/demo.exe",
        title="Demo",
        class_name="DemoWindow",
        bounds=Rect(0, 0, 100, 80),
        window_bounds=Rect(0, 0, 100, 80),
        client_bounds=Rect(0, 0, 100, 80),
    )


def _context(directory: Path, config_path: Path, store: LayoutStore) -> FeatureContext:
    return FeatureContext(
        instance_name="occluder",
        type_name="black_rectangles",
        project_root=directory,
        config_path=config_path,
        assets_dir=directory / "assets",
        logger=logging.getLogger("test.feature"),
        store=store,
    )


class FeatureDiscoveryTests(unittest.TestCase):
    def test_builtin_features_are_discovered(self) -> None:
        load_builtin_features()
        self.assertIn("black_rectangles", registered_feature_types())
        self.assertIn("toolbar", registered_feature_types())
        self.assertIs(feature_class("black_rectangles"), BlackRectanglesFeature)


class BlackRectanglesFeatureTests(unittest.TestCase):
    def _build(self, options=None, *, layout=None, groups="", signature="1920x1080"):
        kwargs = {"groups": groups}
        if layout is not None:
            kwargs["layout"] = layout
        config, directory = make_config(config_text(**kwargs))
        self.addCleanup(cleanup, directory)
        store = LayoutStore(config)
        store.update_signature(signature)
        context = _context(directory, config.path, store)
        feature = BlackRectanglesFeature(context, options or {})
        return feature, store

    def test_draws_the_active_group(self) -> None:
        feature, _ = self._build()
        surface = PixelSurface(100, 80)
        feature.start()
        feature.draw(surface, _target())
        # The demo group's rectangle is x=10 y=20 30x40.
        self.assertEqual(
            surface.buffer[(20 * 100 + 10) * 4 : (20 * 100 + 10) * 4 + 4],
            b"\x00\x00\x00\xff",
        )
        self.assertEqual(
            surface.buffer[(59 * 100 + 39) * 4 : (59 * 100 + 39) * 4 + 4],
            b"\x00\x00\x00\xff",
        )
        self.assertEqual(surface.buffer[0:4], b"\x00\x00\x00\x00")

    def test_nothing_is_drawn_when_no_group_matches(self) -> None:
        feature, store = self._build()
        store.update_signature("3840x2160")
        surface = PixelSurface(100, 80)
        feature.draw(surface, _target())
        self.assertEqual(surface.buffer, bytearray(len(surface.buffer)))

    def test_hidden_shapes_are_not_drawn(self) -> None:
        feature, store = self._build()
        store.set_hidden(True)
        surface = PixelSurface(100, 80)
        feature.draw(surface, _target())
        self.assertEqual(surface.buffer, bytearray(len(surface.buffer)))

    def test_previews_are_drawn_on_top(self) -> None:
        feature, store = self._build()
        store.set_preview([Shape(x=60, y=5, width=20, height=10, name="p")])
        surface = PixelSurface(100, 80)
        feature.draw(surface, _target())
        self.assertEqual(
            surface.buffer[(10 * 100 + 70) * 4 : (10 * 100 + 70) * 4 + 4],
            b"\x00\x00\x00\xff",
        )

    def test_clipping_is_delegated_to_surface(self) -> None:
        groups = """
[groups.clipped]
resolution = "3440x1440"
rectangles = [
  { x = -10, y = -10, width = 15, height = 15 },
]
"""
        feature, _ = self._build(groups=groups, signature="3440x1440")
        surface = PixelSurface(10, 10)
        feature.draw(surface, _target())
        self.assertEqual(surface.buffer[0:4], b"\x00\x00\x00\xff")
        self.assertEqual(
            surface.buffer[(5 * 10 + 5) * 4 : (5 * 10 + 5) * 4 + 4], bytes(4)
        )

    def test_legacy_rectangles_option_is_rejected_with_a_hint(self) -> None:
        with self.assertRaisesRegex(FeatureError, "groups"):
            self._build({"rectangles": []})

    def test_unknown_option_is_rejected(self) -> None:
        with self.assertRaisesRegex(FeatureError, "unknown option"):
            self._build({"oops": 1})

    def test_missing_store_is_rejected(self) -> None:
        config, directory = make_config(config_text())
        self.addCleanup(cleanup, directory)
        context = FeatureContext(
            instance_name="occluder",
            type_name="black_rectangles",
            project_root=directory,
            config_path=config.path,
            assets_dir=directory / "assets",
            logger=logging.getLogger("test.feature"),
        )
        with self.assertRaisesRegex(FeatureError, "layout store"):
            BlackRectanglesFeature(context, {})


class FeatureContextTests(unittest.TestCase):
    def test_repaint_callback_is_available(self) -> None:
        config, directory = make_config(config_text())
        self.addCleanup(cleanup, directory)
        calls: list[int] = []
        context = FeatureContext(
            instance_name="occluder",
            type_name="black_rectangles",
            project_root=directory,
            config_path=config.path,
            assets_dir=directory / "assets",
            logger=logging.getLogger("test.feature"),
            request_repaint=lambda: calls.append(1),
        )
        store = LayoutStore(config)
        feature = BlackRectanglesFeature(
            FeatureContext(
                instance_name=context.instance_name,
                type_name=context.type_name,
                project_root=context.project_root,
                config_path=context.config_path,
                assets_dir=context.assets_dir,
                logger=context.logger,
                request_repaint=context.request_repaint,
                store=store,
            ),
            {},
        )
        feature.request_repaint()
        self.assertEqual(calls, [1])

    def test_asset_path_cannot_escape_assets(self) -> None:
        config, directory = make_config(config_text())
        self.addCleanup(cleanup, directory)
        context = FeatureContext(
            instance_name="occluder",
            type_name="black_rectangles",
            project_root=directory,
            config_path=config.path,
            assets_dir=directory / "assets",
            logger=logging.getLogger("test.feature"),
        )
        self.assertEqual(
            context.asset_path("sprites/a.png"),
            directory / "assets" / "sprites" / "a.png",
        )
        with self.assertRaises(FeatureError):
            context.asset_path("../../outside.png")


if __name__ == "__main__":
    unittest.main()
