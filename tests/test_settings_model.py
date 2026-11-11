"""Headless tests for the settings window model.

Everything here operates on a temporary copy of ``config/config.toml``; the
repository configuration file is never modified and no Tk window is created.
"""

from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any

from screenpainter.config import load_config
from screenpainter.core.keymap import parse_binding
from screenpainter.errors import ConfigError
from screenpainter.settings.model import (
    LOG_LEVELS,
    TOOLBAR_OPTION_KINDS,
    SettingsDraft,
    SettingsValidationError,
    SettingsValueError,
    collect_fields,
    field_kind,
    format_toml_value,
    parse_toml_value,
    validate_group_name,
)
from screenpainter.toml_edit import TomlDocument

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.toml"


class SettingsDraftTestCase(unittest.TestCase):
    """Base class that hands every test a throw-away copy of the example file."""

    draft: SettingsDraft

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="screenpainter-settings-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.toml"
        shutil.copyfile(EXAMPLE_CONFIG, self.path)
        self.draft = SettingsDraft.from_path(self.path)

    # -- helpers ---------------------------------------------------------
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def parsed(self) -> dict[str, Any]:
        return tomllib.loads(self.text())

    def assert_untouched_directory(self) -> None:
        """Only the configuration itself may be left behind."""
        names = sorted(item.name for item in self.directory.iterdir())
        self.assertEqual(names, ["config.toml"], f"stray files: {names}")

    def assert_comment_preserved(self, comment: str) -> None:
        self.assertIn(comment, self.text())


class FieldCollectionTests(SettingsDraftTestCase):
    def test_every_schema_value_is_collected(self) -> None:
        fields = {field.path: field for field in collect_fields(self.draft.raw)}
        for expected in (
            "version",
            "app.name",
            "app.verify_ms",
            "app.debug_interval_ms",
            "logging.level",
            "logging.backup_count",
            "target.window",
            "overlay.coordinate_space",
            "layout.hidden",
            "keys.redo",
            "groups.wechat_1920x1080.description",
            "groups.wechat_1920x1080.resolution",
            "groups.wechat_1920x1080.rectangles",
            "window_profiles.wechat.process_names",
            "window_profiles.wechat.select",
            "features.toolbar.outline_color",
            "features.occluder.type",
        ):
            self.assertIn(expected, fields, f"missing field {expected}")

    def test_field_types(self) -> None:
        fields = {field.path: field for field in collect_fields(self.draft.raw)}
        self.assertEqual(fields["app.verify_ms"].kind, "int")
        self.assertEqual(fields["app.debug"].kind, "bool")
        self.assertEqual(fields["app.name"].kind, "str")
        self.assertEqual(fields["logging.file"].kind, "str")
        self.assertEqual(fields["window_profiles.wechat.title_patterns"].kind, "str_list")
        self.assertEqual(fields["keys.delete"].kind, "str")
        self.assertEqual(fields["groups.wechat_1920x1080.rectangles"].kind, "table_array")
        self.assertEqual(fields["groups.wechat_1920x1080.resolution"].section, ("groups", "wechat_1920x1080"))

    def test_field_kind_classification(self) -> None:
        self.assertEqual(field_kind(True), "bool")
        self.assertEqual(field_kind(3), "int")
        self.assertEqual(field_kind(1.5), "float")
        self.assertEqual(field_kind("text"), "str")
        self.assertEqual(field_kind(["a", "b"]), "str_list")
        self.assertEqual(field_kind([1, 2]), "number_list")
        self.assertEqual(field_kind([{"name": "a"}]), "table_array")
        self.assertEqual(field_kind({"a": 1}), "table")
        self.assertEqual(field_kind([]), "str_list")
        self.assertEqual(field_kind(None), "other")

    def test_section_name_accessors(self) -> None:
        self.assertIn("wechat_1920x1080", self.draft.group_names())
        self.assertIn("wechat_3120x2080", self.draft.group_names())
        self.assertEqual(self.draft.profile_names(), ("wechat",))
        self.assertEqual(self.draft.feature_names(), ("occluder", "toolbar"))

    def test_typed_defaults_and_get(self) -> None:
        self.assertEqual(self.draft.get(("app",), "name"), "ScreenPainter")
        self.assertFalse(self.draft.get(("app",), "debug"))
        self.assertEqual(self.draft.get(("app",), "missing", 42), 42)
        self.assertIn("toolbar", self.draft.get_table(("features",)))
        self.assertEqual(self.draft.get_table(("nope",)), {})


class ScalarEditingTests(SettingsDraftTestCase):
    def test_scalar_change_round_trip_preserves_comments(self) -> None:
        self.assertFalse(self.draft.changed())
        self.draft.set(("app",), "name", "Masker")
        self.draft.set(("app",), "verify_ms", 750)
        self.draft.set(("app",), "debug", True)
        self.draft.set(("logging",), "level", "DEBUG")
        self.assertTrue(self.draft.changed())
        self.assertEqual(
            self.draft.changes(),
            ("app.debug", "app.name", "app.verify_ms", "logging.level"),
        )

        self.assertTrue(self.draft.save())
        self.assertFalse(self.draft.changed())

        config = load_config(self.path)
        self.assertEqual(config.app.name, "Masker")
        self.assertEqual(config.app.verify_ms, 750)
        self.assertTrue(config.app.debug)
        self.assertEqual(config.logging.level, "DEBUG")

        # Comments of sections the edit did not touch must survive verbatim.
        self.assert_comment_preserved("# Keyboard shortcuts")
        self.assert_comment_preserved("# Resolution / rectangle group selection")
        self.assert_comment_preserved("# The floating button panel.")
        self.assertTrue(
            self.text().startswith("# ScreenPainter configuration (schema version 3)")
        )
        self.assert_untouched_directory()

    def test_apply_to_document_only_touches_changed_values(self) -> None:
        document = TomlDocument.from_path(self.path)
        self.assertFalse(self.draft.apply_to_document(document))
        self.draft.set(("target",), "window", "wechat")
        self.assertFalse(self.draft.changed())
        self.draft.set(("logging",), "backup_count", 7)
        self.assertTrue(self.draft.apply_to_document(document))
        data = tomllib.loads(document.text())
        self.assertEqual(data["logging"]["backup_count"], 7)
        self.assertEqual(data["logging"]["level"], "INFO")
        self.assertIn("# Show a small panel", document.text())

    def test_reload_discards_staged_changes(self) -> None:
        self.draft.set(("app",), "name", "Changed")
        self.assertTrue(self.draft.changed())
        self.draft.reload()
        self.assertFalse(self.draft.changed())
        self.assertEqual(self.draft.get(("app",), "name"), "ScreenPainter")
        self.assertFalse(self.draft.save())

    def test_nothing_to_save_returns_false(self) -> None:
        self.assertFalse(self.draft.save())
        self.assert_untouched_directory()


class RectangleEditingTests(SettingsDraftTestCase):
    def test_add_remove_and_recolour_rectangles(self) -> None:
        rows = self.draft.rectangles("wechat_1920x1080")
        self.assertEqual(len(rows), 3)
        self.draft.set_rectangles(
            "wechat_1920x1080",
            [
                {**rows[0], "color": "#123456"},
                rows[1],
                {
                    "name": "extra",
                    "x": 1,
                    "y": 2,
                    "width": 33,
                    "height": 44,
                    "color": "#00FF00",
                    "opacity": 0.5,
                },
            ],
        )
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        group = config.find_group("wechat_1920x1080")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual([shape.name for shape in group.shapes], ["name", "avatar", "extra"])
        self.assertEqual(group.shapes[0].color, "#123456")
        self.assertEqual(group.shapes[2].width, 33)
        self.assertEqual(group.shapes[2].opacity, 0.5)

        # The other resolution group must be untouched, value for value.
        other = config.find_group("wechat_3120x2080")
        assert other is not None
        self.assertEqual(len(other.shapes), 4)
        self.assertEqual(other.shapes[0].color, "#FAFAFA")

        data = self.parsed()
        self.assertEqual(len(data["groups"]["wechat_1920x1080"]["rectangles"]), 3)
        self.assert_comment_preserved("# One group per resolution.")
        self.assert_untouched_directory()

    def test_rectangles_rewritten_as_parsable_inline_tables(self) -> None:
        rows = self.draft.rectangles("wechat_3120x2080")
        self.draft.set_rectangles("wechat_3120x2080", rows[:2])
        document = TomlDocument.from_path(self.path)
        self.draft.apply_to_document(document)
        data = tomllib.loads(document.text())
        self.assertEqual(len(data["groups"]["wechat_3120x2080"]["rectangles"]), 2)
        self.assertEqual(data["groups"]["wechat_3120x2080"]["rectangles"][0]["name"], "name")


class GroupSectionTests(SettingsDraftTestCase):
    def test_add_group_with_rectangles(self) -> None:
        name = self.draft.add_group(
            "temp_group",
            resolution="1280x720",
            description="temporary",
            rectangles=[
                {
                    "name": "only",
                    "x": 5,
                    "y": 6,
                    "width": 7,
                    "height": 8,
                    "color": "#102030",
                    "opacity": 1.0,
                }
            ],
        )
        self.assertEqual(name, "temp_group")
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        group = config.find_group("temp_group")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group.resolution, "1280x720")
        self.assertEqual(group.description, "temporary")
        self.assertEqual(len(group.shapes), 1)
        self.assertIn("[groups.temp_group]", self.text())
        self.assert_comment_preserved("# Window profiles")

    def test_remove_group(self) -> None:
        draft = SettingsDraft.from_path(self.path)
        draft.remove_group("wechat_3120x2080")
        self.assertTrue(draft.save())
        self.assertIsNone(load_config(self.path).find_group("wechat_3120x2080"))
        self.assertNotIn("[groups.wechat_3120x2080]", self.text())
        self.assertIsNotNone(load_config(self.path).find_group("wechat_1920x1080"))

    def test_rename_group_and_follow_layout_group(self) -> None:
        self.draft.set(("layout",), "group", "wechat_1920x1080")
        renamed = self.draft.rename_group("wechat_1920x1080", "wechat_main")
        self.draft.set(("layout",), "group", renamed)
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        self.assertIsNone(config.find_group("wechat_1920x1080"))
        group = config.find_group("wechat_main")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(len(group.shapes), 3)
        self.assertEqual(config.layout.group, "wechat_main")

    def test_duplicate_group_name_is_rejected(self) -> None:
        with self.assertRaises(SettingsValueError):
            self.draft.add_group("wechat_1920x1080", resolution="1920x1080")
        with self.assertRaises(SettingsValueError):
            validate_group_name("bad name")
        with self.assertRaises(SettingsValueError):
            validate_group_name(".hidden")
        self.assertEqual(validate_group_name("ok_group-1"), "ok_group-1")


class WindowProfileTests(SettingsDraftTestCase):
    def test_add_profile_and_switch_target(self) -> None:
        self.draft.add_profile(
            "notepad",
            description="Windows Notepad",
            process_names=["notepad.exe"],
            title_patterns=["^Notepad$"],
            exclude_title_patterns=["^Notepad - 记事本$"],
            min_width=200,
            min_height=100,
            select="first",
        )
        self.draft.set(("target",), "window", "notepad")
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        self.assertEqual(config.target_profile_name, "notepad")
        profile = config.target_profile
        self.assertEqual(profile.process_names, ("notepad.exe",))
        self.assertEqual(
            [pattern.pattern for pattern in profile.exclude_title_patterns],
            ["^Notepad - 记事本$"],
        )
        self.assertEqual(profile.min_width, 200)
        self.assertEqual(profile.select, "first")
        self.assertEqual(config.target_profile.name, "notepad")
        self.assertIn("wechat", config.window_profiles)
        self.assert_comment_preserved("# Add more profiles here, for example:")

    def test_remove_profile_and_switch_target_back(self) -> None:
        self.draft.add_profile("second", process_names=["second.exe"])
        self.assertTrue(self.draft.save())
        self.assertIn("second", load_config(self.path).window_profiles)

        draft = SettingsDraft.from_path(self.path)
        draft.remove_profile("second")
        draft.set(("target",), "window", "wechat")
        self.assertTrue(draft.save())
        config = load_config(self.path)
        self.assertEqual(tuple(config.window_profiles), ("wechat",))
        self.assertNotIn("[window_profiles.second]", self.text())


class ShortcutTests(SettingsDraftTestCase):
    def test_delete_and_undo_round_trip(self) -> None:
        self.draft.set(("keys",), "delete", "Ctrl+D")
        self.draft.set(("keys",), "undo", "Ctrl+U")
        self.draft.set(("keys",), "redo", "Ctrl+Shift+Y")
        self.assertTrue(self.draft.save())

        # ``load_config`` validates [keys] (an invalid binding fails the save),
        # so read the stored text and check the canonical round trip directly.
        config = load_config(self.path)
        keys = self.parsed()["keys"]
        self.assertEqual(parse_binding(keys["delete"]).describe(), "Ctrl+D")
        self.assertEqual(parse_binding(keys["undo"]).describe(), "Ctrl+U")
        self.assertEqual(parse_binding(keys["redo"]).describe(), "Ctrl+Shift+Y")
        for action in ("delete", "undo", "redo"):
            binding = parse_binding(keys[action])
            self.assertEqual(parse_binding(binding.describe()), binding)
        self.assertEqual(config.keys.cancel.describe(), "Esc")
        self.assert_comment_preserved("# Remove the selected rectangle.")

    def test_invalid_shortcut_is_rejected(self) -> None:
        self.draft.set(("keys",), "delete", "Ctrl+NotAKey")
        with self.assertRaises(ConfigError):
            self.draft.save()
        self.assert_untouched_directory()


class ValidationTests(SettingsDraftTestCase):
    def test_invalid_value_is_rejected_and_file_untouched(self) -> None:
        original = self.path.read_bytes()
        self.draft.set(("app",), "verify_ms", 5)
        self.assertTrue(self.draft.changed())
        with self.assertRaises(SettingsValidationError) as raised:
            self.draft.save()
        self.assertIn("verify_ms", str(raised.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_untouched_directory()
        # The staged change is still there, so the user can fix and retry.
        self.assertTrue(self.draft.changed())
        self.assertEqual(self.draft.get(("app",), "verify_ms"), 5)
        self.draft.set(("app",), "verify_ms", 1500)
        self.assertTrue(self.draft.save())
        self.assertEqual(load_config(self.path).app.verify_ms, 1500)

    def test_invalid_log_level_reports_key(self) -> None:
        original = self.path.read_bytes()
        self.draft.set(("logging",), "level", "TRACE")
        with self.assertRaises(SettingsValidationError) as raised:
            self.draft.save()
        self.assertIn("logging.level", str(raised.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_untouched_directory()

    def test_validate_checks_without_writing(self) -> None:
        self.draft.set(("layout",), "mode", "manual")
        self.draft.set(("layout",), "group", "does_not_exist")
        with self.assertRaises(SettingsValidationError):
            self.draft.validate()
        self.assertFalse(load_config(self.path).layout.mode == "manual")
        self.assert_untouched_directory()
        self.draft.set(("layout",), "group", "wechat_3120x2080")
        self.draft.validate()  # now valid
        self.assertEqual(load_config(self.path).layout.mode, "auto")

    def test_temp_file_name_is_not_the_real_file(self) -> None:
        self.draft.set(("app",), "verify_ms", 5)
        with self.assertRaises(SettingsValidationError):
            self.draft.save()
        self.assertFalse(
            (self.directory / "config.toml.settings-check.toml").exists()
        )
        self.assert_untouched_directory()


class FeatureOptionTests(SettingsDraftTestCase):
    def test_toggle_toolbar_options(self) -> None:
        self.draft.set(("features", "toolbar"), "properties_panel", False)
        self.draft.set(("features", "toolbar"), "buttons", ["edit", "hide", "settings"])
        self.draft.set(("features", "toolbar"), "outline_width", 2)
        self.draft.set(("features", "toolbar"), "default_opacity", 0.75)
        self.draft.set(("features", "toolbar"), "title", "遮罩工具条")
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        toolbar = next(item for item in config.features if item.name == "toolbar")
        self.assertFalse(toolbar.options["properties_panel"])
        self.assertEqual(toolbar.options["buttons"], ["edit", "hide", "settings"])
        self.assertEqual(toolbar.options["outline_width"], 2)
        self.assertEqual(toolbar.options["default_opacity"], 0.75)
        self.assertEqual(toolbar.options["title"], "遮罩工具条")
        self.assert_comment_preserved("# The floating button panel.")
        self.assert_untouched_directory()

    def test_delete_option_key(self) -> None:
        self.draft.delete(("features", "toolbar"), "handle_size")
        self.assertTrue(self.draft.changed())
        self.assertIn("features.toolbar.handle_size", self.draft.changes())
        self.assertTrue(self.draft.save())
        config = load_config(self.path)
        toolbar = next(item for item in config.features if item.name == "toolbar")
        self.assertNotIn("handle_size", toolbar.options)
        self.assertNotIn("handle_size", self.text())

    def test_add_and_remove_feature_table(self) -> None:
        self.draft.add_feature(
            "second_toolbar",
            feature_type="toolbar",
            enabled=False,
            options={"title": "第二个面板", "x": 10},
        )
        self.draft.set(("features", "second_toolbar"), "y", 20)
        self.assertTrue(self.draft.save())

        config = load_config(self.path)
        names = [item.name for item in config.features]
        self.assertIn("second_toolbar", names)
        second = next(item for item in config.features if item.name == "second_toolbar")
        self.assertFalse(second.enabled)
        self.assertEqual(second.type, "toolbar")
        self.assertEqual(second.options["title"], "第二个面板")
        self.assertEqual(second.options["x"], 10)
        self.assertEqual(second.options["y"], 20)

        draft = SettingsDraft.from_path(self.path)
        draft.remove_feature("second_toolbar")
        self.assertTrue(draft.save())
        names = [item.name for item in load_config(self.path).features]
        self.assertNotIn("second_toolbar", names)
        self.assertNotIn("[features.second_toolbar]", self.text())

    def test_toolbar_option_schema_covers_the_example(self) -> None:
        data = self.parsed()
        toolbar = data["features"]["toolbar"]
        for key in ("buttons", "auto_position", "x", "y", "default_color"):
            self.assertIn(key, TOOLBAR_OPTION_KINDS)
            self.assertIn(key, toolbar)
        for key in ("outline_color", "selection_width", "handle_size", "properties_panel"):
            self.assertIn(key, TOOLBAR_OPTION_KINDS)
            self.assertIn(key, toolbar)


class TomlValueHelperTests(unittest.TestCase):
    def test_format_value(self) -> None:
        self.assertEqual(format_toml_value(42), "42")
        self.assertEqual(format_toml_value(True), "true")
        self.assertEqual(format_toml_value("x"), '"x"')
        self.assertEqual(format_toml_value(["a", "b"]), '["a", "b"]')
        self.assertEqual(format_toml_value({"a": 1, "b": ["x"]}), '{ a = 1, b = ["x"] }')
        self.assertEqual(
            format_toml_value([{"name": "a", "x": 1}]),
            '[{ name = "a", x = 1 }]',
        )

    def test_parse_value_round_trip(self) -> None:
        for value in (42, -1, 1.5, True, False, "text", ["a", "b"], {"a": 1, "b": [2]}):
            self.assertEqual(parse_toml_value(format_toml_value(value)), value)
        self.assertEqual(parse_toml_value(" [] "), [])

    def test_parse_value_rejects_garbage(self) -> None:
        with self.assertRaises(SettingsValueError):
            parse_toml_value("not a value")
        with self.assertRaises(SettingsValueError):
            parse_toml_value("   ")


class ModuleConstantTests(unittest.TestCase):
    def test_choices_match_the_loader(self) -> None:
        self.assertIn("INFO", LOG_LEVELS)
        self.assertEqual(len(LOG_LEVELS), 5)
        # Every offered log level must be accepted by the real loader.
        directory = tempfile.TemporaryDirectory(prefix="screenpainter-settings-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.toml"
        for level in LOG_LEVELS:
            text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
            path.write_text(
                text.replace('level = "INFO"', f'level = "{level}"', 1), encoding="utf-8"
            )
            self.assertEqual(load_config(path).logging.level, level)


class KeyCaptureTests(unittest.TestCase):
    """The shortcut capture helper is pure; importing tkinter is enough here."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from screenpainter.settings import app as settings_app
        except Exception as exc:  # pragma: no cover - tkinter missing
            raise unittest.SkipTest(f"tkinter is not available: {exc}") from exc
        cls.app = settings_app

    def test_canonical_combinations(self) -> None:
        mapping = self.app.keysym_to_binding
        self.assertEqual(mapping("z", 0x0004), "Ctrl+Z")
        self.assertEqual(mapping("z", 0x0004 | 0x0001), "Ctrl+Shift+Z")
        self.assertEqual(mapping("Left", 0x0001), "Shift+Left")
        self.assertEqual(mapping("Tab", 0x0001), "Shift+Tab")
        self.assertEqual(mapping("Delete", 0x0000), "Delete")
        self.assertEqual(mapping("Escape", 0x0000), "Esc")
        self.assertEqual(mapping("F5", 0x0000), "F5")
        self.assertEqual(mapping("plus", 0x0004), "Ctrl++")
        self.assertEqual(mapping("space", 0x0000), "Space")

    def test_modifier_only_presses_are_ignored(self) -> None:
        mapping = self.app.keysym_to_binding
        for keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Win_L"):
            self.assertIsNone(mapping(keysym, 0x0001 | 0x0004), keysym)

    def test_unknown_key_is_ignored(self) -> None:
        self.assertIsNone(self.app.keysym_to_binding("XF86AudioMute", 0x0000))


if __name__ == "__main__":
    unittest.main()
