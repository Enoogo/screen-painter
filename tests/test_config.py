"""Tests for config parsing, defaults and validation errors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from screenpainter.config import DEFAULT_CONFIG_PATH, load_config
from screenpainter.errors import ConfigError

VALID = """\
version = 3

[app]
name = "test"
verify_ms = 1000
idle_tick_ms = 500
debug = false
debug_interval_ms = 400

[keys]
cancel = "Escape"
confirm = "Enter"
delete = "Delete"
undo = "Ctrl+Z"
redo = "Ctrl+Shift+Z"
select_next = "Tab"
select_prev = "Shift+Tab"
move_left = "Left"
move_right = "Right"
move_up = "Up"
move_down = "Down"
move_left_fast = "Shift+Left"
move_right_fast = "Shift+Right"
move_up_fast = "Shift+Up"
move_down_fast = "Shift+Down"

[logging]
level = "DEBUG"
file = ""

[target]
window = "demo"

[overlay]
coordinate_space = "window"
click_through = true

[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = false

[groups.demo]
description = "demo"
resolution = "1920x1080"
rectangles = [
  { name = "one", x = 10, y = 20, width = 30, height = 40 },
]

[window_profiles.demo]
description = "demo"
process_names = ["demo.exe"]
title_patterns = ["^Demo"]
class_names = ["DemoWindow"]
min_width = 100
min_height = 80
select = "largest"

[features.main]
type = "black_rectangles"
enabled = true
"""


class ConfigTests(unittest.TestCase):
    def _load_text(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_default_config_is_valid(self) -> None:
        config = load_config(DEFAULT_CONFIG_PATH)
        self.assertEqual(config.target_profile_name, "wechat")
        self.assertEqual(config.overlay.coordinate_space, "window")
        types = [feature.type for feature in config.features]
        self.assertIn("black_rectangles", types)
        self.assertIn("toolbar", types)
        self.assertEqual(config.layout.mode, "auto")
        self.assertEqual(config.layout.match, "monitor")
        self.assertFalse(config.app.debug)
        self.assertTrue(config.groups)
        for group in config.groups:
            self.assertEqual(group.resolution, group.resolution.lower())
            self.assertGreater(len(group.shapes), 0)

    def test_example_config_is_valid(self) -> None:
        config = load_config(DEFAULT_CONFIG_PATH.parent / "examples" / "notepad.toml")
        self.assertEqual(config.target_profile_name, "notepad")
        self.assertEqual(config.target_profile.process_names, ("notepad.exe",))
        self.assertIsNotNone(config.find_group("notepad_1920x1080"))

    def test_valid_config(self) -> None:
        config = self._load_text(VALID)
        self.assertEqual(config.app.name, "test")
        self.assertEqual(config.logging.file, None)
        self.assertEqual(config.target_profile.class_names, ("DemoWindow",))
        self.assertIsNone(config.target_profile.title_patterns[0].search("nope"))
        group = config.find_group("demo")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group.resolution, "1920x1080")
        self.assertEqual(len(group.shapes), 1)
        self.assertEqual(group.shapes[0].width, 30)
        self.assertEqual(group.shapes[0].color, "#000000")
        self.assertIsNone(config.find_group("missing"))

    def test_unknown_top_level_key(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(VALID + "\n[unexpected]\nvalue = 1\n")

    def test_missing_target_profile(self) -> None:
        text = VALID.replace('window = "demo"', 'window = "missing"')
        with self.assertRaisesRegex(ConfigError, "window_profiles"):
            self._load_text(text)

    def test_empty_profile_rejected(self) -> None:
        text = VALID.replace('process_names = ["demo.exe"]', "process_names = []")
        text = text.replace('title_patterns = ["^Demo"]', "title_patterns = []")
        text = text.replace('class_names = ["DemoWindow"]', "class_names = []")
        with self.assertRaisesRegex(ConfigError, "at least one"):
            self._load_text(text)

    def test_invalid_regex(self) -> None:
        text = VALID.replace('title_patterns = ["^Demo"]', 'title_patterns = ["("]')
        with self.assertRaisesRegex(ConfigError, "regular expression"):
            self._load_text(text)

    def test_invalid_overlay_mode(self) -> None:
        text = VALID.replace('coordinate_space = "window"', 'coordinate_space = "screen"')
        with self.assertRaisesRegex(ConfigError, "coordinate_space"):
            self._load_text(text)

    def test_feature_requires_type(self) -> None:
        text = VALID.replace('type = "black_rectangles"', "")
        with self.assertRaisesRegex(ConfigError, "type"):
            self._load_text(text)

    def test_unknown_profile_key(self) -> None:
        text = VALID.replace('select = "largest"', 'select = "largest"\nunsupported = 1')
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(text)

    def test_bom_is_tolerated(self) -> None:
        # The loader decodes with utf-8-sig so editors that add a BOM do not
        # break the TOML parser.
        config = self._load_text("\ufeff" + VALID)
        self.assertEqual(config.app.name, "test")

    def test_missing_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "not found"):
            load_config(Path(tempfile.gettempdir()) / "definitely-not-here-screenpainter.toml")

    def test_invalid_toml(self) -> None:
        with self.assertRaisesRegex(ConfigError, "invalid TOML"):
            self._load_text("[app\nname = 'x'")

    # -- schema version ------------------------------------------------
    def test_version_1_is_rejected_with_migration_hint(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Version 1 stored rectangles"):
            self._load_text(VALID.replace("version = 3", "version = 1"))

    def test_missing_version_defaults_to_current_schema(self) -> None:
        config = self._load_text(VALID.replace("version = 3\n", ""))
        self.assertEqual(config.app.name, "test")

    # -- debug mode ----------------------------------------------------
    def test_debug_defaults_are_off(self) -> None:
        text = VALID.replace("debug = false\n", "").replace("debug_interval_ms = 400\n", "")
        config = self._load_text(text)
        self.assertFalse(config.app.debug)
        self.assertEqual(config.app.debug_interval_ms, 400)

    def test_debug_interval_bounds(self) -> None:
        for value in (50, 6000):
            with self.subTest(value=value):
                text = VALID.replace("debug_interval_ms = 400", f"debug_interval_ms = {value}")
                with self.assertRaisesRegex(ConfigError, "debug_interval_ms"):
                    self._load_text(text)

    def test_debug_must_be_boolean(self) -> None:
        text = VALID.replace("debug = false", 'debug = "yes"')
        with self.assertRaisesRegex(ConfigError, "debug"):
            self._load_text(text)

    def test_unknown_app_key(self) -> None:
        text = VALID.replace("debug = false", "debug = false\nhot_reload = true")
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(text)

    # -- keys ----------------------------------------------------------
    def test_keys_are_parsed_and_used(self) -> None:
        config = self._load_text(
            VALID.replace('delete = "Delete"', 'delete = "Backspace"')
        )
        self.assertEqual(config.keys.delete.describe(), "Backspace")
        self.assertEqual(config.keys.undo.describe(), "Ctrl+Z")

    def test_keys_defaults_apply_when_section_is_missing(self) -> None:
        head, rest = VALID.split("[keys]", 1)
        text = head + "[logging]" + rest.split("[logging]", 1)[1]
        config = self._load_text(text)
        self.assertEqual(config.keys.delete.describe(), "Delete")

    def test_invalid_shortcut_is_rejected(self) -> None:
        text = VALID.replace('delete = "Delete"', 'delete = "NoSuchKey"')
        with self.assertRaisesRegex(ConfigError, "keys.delete"):
            self._load_text(text)

    def test_duplicate_shortcuts_are_rejected(self) -> None:
        text = VALID.replace('redo = "Ctrl+Shift+Z"', 'redo = "Ctrl+Z"')
        with self.assertRaisesRegex(ConfigError, "duplicate shortcut"):
            self._load_text(text)

    # -- layout --------------------------------------------------------
    def test_unknown_layout_key(self) -> None:
        text = VALID.replace('match = "monitor"', 'match = "monitor"\nresolution = "1920x1080"')
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(text)

    def test_invalid_layout_mode(self) -> None:
        text = VALID.replace('mode = "auto"', 'mode = "fixed"')
        with self.assertRaisesRegex(ConfigError, "layout.mode"):
            self._load_text(text)

    def test_invalid_layout_match(self) -> None:
        text = VALID.replace('match = "monitor"', 'match = "screen"')
        with self.assertRaisesRegex(ConfigError, "layout.match"):
            self._load_text(text)

    def test_manual_mode_requires_group(self) -> None:
        text = VALID.replace('mode = "auto"', 'mode = "manual"')
        with self.assertRaisesRegex(ConfigError, "layout.group"):
            self._load_text(text)

    def test_manual_mode_requires_existing_group(self) -> None:
        text = VALID.replace('mode = "auto"', 'mode = "manual"')
        text = text.replace('group = ""', 'group = "nope"')
        with self.assertRaisesRegex(ConfigError, "groups"):
            self._load_text(text)

    def test_manual_mode_with_group_is_accepted(self) -> None:
        text = VALID.replace('mode = "auto"', 'mode = "manual"')
        text = text.replace('group = ""', 'group = "demo"')
        config = self._load_text(text)
        self.assertEqual(config.layout.mode, "manual")
        self.assertEqual(config.layout.group, "demo")

    def test_unknown_group_reference_in_auto_mode(self) -> None:
        text = VALID.replace('group = ""', 'group = "nope"')
        with self.assertRaisesRegex(ConfigError, "groups"):
            self._load_text(text)

    def test_two_groups_may_share_a_resolution(self) -> None:
        extra = """\

[groups.demo_alt]
resolution = "1920x1080"
rectangles = []
"""
        config = self._load_text(VALID + extra)
        self.assertEqual(
            [group.name for group in config.groups if group.resolution == "1920x1080"],
            ["demo", "demo_alt"],
        )

    def test_group_name_is_validated(self) -> None:
        text = VALID.replace("[groups.demo]", '[groups."bad name!"]')
        with self.assertRaisesRegex(ConfigError, "group name"):
            self._load_text(text)

    def test_group_missing_resolution(self) -> None:
        text = VALID.replace('resolution = "1920x1080"\n', "")
        with self.assertRaisesRegex(ConfigError, "resolution"):
            self._load_text(text)

    def test_group_invalid_resolution(self) -> None:
        text = VALID.replace('resolution = "1920x1080"', 'resolution = "wide"')
        with self.assertRaisesRegex(ConfigError, "resolution"):
            self._load_text(text)

    def test_group_unknown_key(self) -> None:
        text = VALID.replace('description = "demo"', 'description = "demo"\noops = 1')
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(text)

    def test_groups_must_be_a_table(self) -> None:
        text = VALID.replace("[groups.demo]", "groups = 1\n\n[groups.demo]")
        with self.assertRaisesRegex(ConfigError, "groups"):
            self._load_text(text)

    # -- rectangles ----------------------------------------------------
    def test_invalid_rectangles(self) -> None:
        cases = {
            "width zero": '{ x = 0, y = 0, width = 0, height = 10 }',
            "missing height": '{ x = 0, y = 0, width = 10 }',
            "bad opacity": '{ x = 0, y = 0, width = 10, height = 10, opacity = 2 }',
            "bad color": '{ x = 0, y = 0, width = 10, height = 10, color = "black" }',
        }
        for label, rectangle in cases.items():
            with self.subTest(label):
                text = VALID.replace(
                    '{ name = "one", x = 10, y = 20, width = 30, height = 40 }',
                    rectangle,
                )
                with self.assertRaises(ConfigError):
                    self._load_text(text)

    def test_unknown_rectangle_key(self) -> None:
        text = VALID.replace(
            '{ name = "one", x = 10, y = 20, width = 30, height = 40 }',
            '{ x = 0, y = 0, width = 10, height = 10, colour = "#000000" }',
        )
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self._load_text(text)

    def test_rectangle_name_defaults(self) -> None:
        text = VALID.replace(
            '{ name = "one", x = 10, y = 20, width = 30, height = 40 }',
            '{ x = 10, y = 20, width = 30, height = 40 }',
        )
        config = self._load_text(text)
        group = config.find_group("demo")
        assert group is not None
        self.assertEqual(group.shapes[0].name, "rectangle_1")


if __name__ == "__main__":
    unittest.main()
