"""Tests for the layout store, its persistence and the resolution logic."""

from __future__ import annotations

import unittest
from pathlib import Path

from screenpainter.core.layout import (
    LayoutGroup,
    LayoutSettings,
    LayoutStore,
    Shape,
    save_new_group,
)
from screenpainter.toml_edit import TomlDocument

try:  # ``python -m unittest discover -s tests`` imports this as a top module
    from .helpers import cleanup, config_text, make_config
except ImportError:  # pragma: no cover - depends on how the tests are run
    from helpers import cleanup, config_text, make_config


class ShapeTests(unittest.TestCase):
    def test_translated(self) -> None:
        shape = Shape(x=1, y=2, width=3, height=4, name="a")
        moved = shape.translated(10, -5)
        self.assertEqual((moved.x, moved.y, moved.width, moved.height), (11, -3, 3, 4))
        self.assertEqual((shape.x, shape.y), (1, 2))

    def test_color_value_uses_opacity(self) -> None:
        shape = Shape(x=0, y=0, width=1, height=1, color="#FF0000", opacity=0.5)
        color = shape.color_value()
        self.assertEqual((color.r, color.g, color.b, color.alpha), (255, 0, 0, 128))

    def test_to_mapping_key_order(self) -> None:
        mapping = Shape(x=1, y=2, width=3, height=4, name="n").to_mapping()
        self.assertEqual(
            list(mapping),
            ["name", "x", "y", "width", "height", "color", "opacity"],
        )


class LayoutStoreTests(unittest.TestCase):
    def _store(self, **kwargs) -> tuple[LayoutStore, Path]:
        config, directory = make_config(config_text(**kwargs))
        self.addCleanup(cleanup, directory)
        store = LayoutStore(config)
        return store, directory

    def test_auto_mode_selects_group_by_signature(self) -> None:
        groups = """
[groups.second]
resolution = "2560x1440"
rectangles = []
"""
        store, _ = self._store(groups=groups)
        self.assertEqual(store.signature, "")
        self.assertIsNone(store.active_group_name)
        self.assertTrue(store.update_signature("2560x1440"))
        self.assertEqual(store.active_group_name, "second")
        self.assertFalse(store.update_signature("2560x1440"))
        self.assertTrue(store.update_signature("1920x1080"))
        self.assertEqual(store.active_group_name, "demo")
        self.assertEqual(len(store.active_shapes()), 1)

    def test_unknown_signature_has_no_active_group(self) -> None:
        store, _ = self._store()
        store.update_signature("3840x2160")
        self.assertIsNone(store.active_group_name)
        self.assertEqual(store.active_shapes(), ())

    def test_manual_mode_uses_configured_group_regardless_of_signature(self) -> None:
        layout = """\
[layout]
mode = "manual"
match = "monitor"
group = "demo"
hidden = false
"""
        store, _ = self._store(layout=layout)
        self.assertEqual(store.active_group_name, "demo")
        store.update_signature("2560x1440")
        self.assertEqual(store.active_group_name, "demo")

    def test_draw_shapes_respects_hidden_and_preview(self) -> None:
        store, _ = self._store()
        store.update_signature("1920x1080")
        self.assertEqual(len(store.draw_shapes()), 1)
        preview = Shape(x=0, y=0, width=5, height=5, name="preview")
        store.set_preview([preview])
        self.assertEqual(len(store.draw_shapes()), 2)
        store.clear_preview()
        self.assertEqual(len(store.draw_shapes()), 1)
        # Hidden hides saved shapes, but previews stay visible for editing.
        store.set_hidden(True)
        self.assertEqual(store.draw_shapes(), ())
        store.set_preview([preview])
        self.assertEqual(store.draw_shapes(), (preview,))
        store.clear_preview()
        store.set_hidden(False)
        self.assertEqual(len(store.draw_shapes()), 1)

    def test_preview_can_replace_the_base(self) -> None:
        store, _ = self._store()
        store.update_signature("1920x1080")
        replacement = Shape(x=1, y=1, width=2, height=2, name="moved")
        store.set_preview([replacement], replace_base=True)
        self.assertEqual(store.draw_shapes(), (replacement,))

    def test_set_hidden_persists_to_the_configuration(self) -> None:
        store, _ = self._store()
        self.assertTrue(store.set_hidden(True))
        self.assertFalse(store.set_hidden(True))
        text = TomlDocument.from_path(store.config_path).text()
        self.assertIn("hidden = true", text)
        # The rest of the document survives the edit.
        self.assertIn("[groups.demo]", text)
        self.assertIn('resolution = "1920x1080"', text)

    def test_add_shapes_creates_a_group_when_none_matches(self) -> None:
        store, directory = self._store()
        store.update_signature("3440x1440")
        self.assertIsNone(store.active_group_name)
        shape = Shape(x=5, y=6, width=70, height=80, name="new")
        added = store.add_shapes([shape])
        self.assertEqual(added, 1)
        self.assertEqual(store.active_group_name, "auto_3440x1440")
        group = store.active_group
        assert group is not None
        self.assertEqual(group.resolution, "3440x1440")
        self.assertEqual(len(group.shapes), 1)

        from screenpainter.config import load_config

        reloaded = load_config(store.config_path)
        group = reloaded.find_group("auto_3440x1440")
        assert group is not None
        self.assertEqual(group.resolution, "3440x1440")
        self.assertEqual(
            (group.shapes[0].x, group.shapes[0].y, group.shapes[0].width),
            (5, 6, 70),
        )
        # The document still parses and keeps the other groups.
        self.assertIsNotNone(reloaded.find_group("demo"))
        cleanup(directory)

    def test_add_shapes_needs_a_signature(self) -> None:
        store, _ = self._store()
        self.assertEqual(store.add_shapes([Shape(x=0, y=0, width=1, height=1)]), 0)

    def test_add_shapes_appends_to_the_active_group(self) -> None:
        store, _ = self._store()
        store.update_signature("1920x1080")
        store.add_shapes([Shape(x=200, y=200, width=10, height=10, name="two")])

        from screenpainter.config import load_config

        reloaded = load_config(store.config_path)
        group = reloaded.find_group("demo")
        assert group is not None
        self.assertEqual([shape.name for shape in group.shapes], ["one", "two"])

    def test_replace_shapes_persists(self) -> None:
        store, _ = self._store()
        store.update_signature("1920x1080")
        moved = [Shape(x=99, y=98, width=30, height=40, name="one")]
        self.assertTrue(store.replace_shapes(moved))
        self.assertFalse(store.replace_shapes(moved))

        from screenpainter.config import load_config

        reloaded = load_config(store.config_path)
        group = reloaded.find_group("demo")
        assert group is not None
        self.assertEqual((group.shapes[0].x, group.shapes[0].y), (99, 98))

    def test_set_active_group_persists_manual_mode(self) -> None:
        groups = """
[groups.second]
resolution = "2560x1440"
rectangles = []
"""
        store, _ = self._store(groups=groups)
        self.assertTrue(store.set_active_group("second"))

        from screenpainter.config import load_config

        reloaded = load_config(store.config_path)
        self.assertEqual(reloaded.layout.mode, "manual")
        self.assertEqual(reloaded.layout.group, "second")

    def test_set_active_group_rejects_unknown(self) -> None:
        store, _ = self._store()
        with self.assertRaises(Exception):
            store.set_active_group("nope")

    def test_apply_config_replaces_groups_and_keeps_identity(self) -> None:
        store, directory = self._store()
        store.update_signature("1920x1080")
        self.assertEqual(store.active_group_name, "demo")

        groups = """
[groups.other]
resolution = "2560x1440"
rectangles = []
"""
        config, second_dir = make_config(config_text(groups=groups))
        self.addCleanup(cleanup, second_dir)
        store.apply_config(config)
        self.assertTrue(store.update_signature("2560x1440"))
        self.assertEqual(store.active_group_name, "other")
        self.assertEqual(store.active_shapes(), ())


class LayoutSettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = LayoutSettings()
        self.assertEqual(settings.mode, "auto")
        self.assertEqual(settings.match, "monitor")
        self.assertEqual(settings.group, "")
        self.assertFalse(settings.hidden)

    def test_group_defaults(self) -> None:
        group = LayoutGroup(name="a", resolution="1920x1080", description="d")
        self.assertEqual(group.shapes, ())
        self.assertEqual(group.description, "d")


class PersistenceHelperTests(unittest.TestCase):
    def test_new_group_and_rectangles_round_trip(self) -> None:
        config, directory = make_config(config_text())
        self.addCleanup(cleanup, directory)
        path = config.path
        save_new_group(
            path,
            name="extra",
            resolution="2560x1440",
            description="second layout",
            shapes=[Shape(x=1, y=2, width=3, height=4, name="s", opacity=0.25)],
        )
        from screenpainter.config import load_config

        reloaded = load_config(path)
        group = reloaded.find_group("extra")
        assert group is not None
        self.assertEqual(group.resolution, "2560x1440")
        self.assertEqual(group.description, "second layout")
        self.assertEqual(len(group.shapes), 1)
        self.assertEqual(group.shapes[0].opacity, 0.25)

    def test_new_group_refuses_duplicates(self) -> None:
        from screenpainter.errors import ConfigWriteError

        config, directory = make_config(config_text())
        self.addCleanup(cleanup, directory)
        with self.assertRaises(ConfigWriteError):
            save_new_group(config.path, name="demo", resolution="1920x1080")


if __name__ == "__main__":
    unittest.main()
