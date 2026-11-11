"""Tests for the comment-preserving TOML editor."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from screenpainter.errors import ConfigWriteError
from screenpainter.toml_edit import TomlDocument, format_value

SAMPLE = """\
# top comment
version = 2

[app]
name = "x"    # inline comment
tick_ms = 16

[layout]
# auto or manual
mode = "auto"
group = ""

[groups.demo]
description = "demo"
resolution = "1920x1080"
rectangles = [
  # a comment inside the array
  { name = "one", x = 1, y = 2, width = 3, height = 4 },
]

[features.toolbar]
type = "toolbar"
enabled = true
list = ["a", "b"]
"""


class FormatValueTests(unittest.TestCase):
    def test_scalars(self) -> None:
        self.assertEqual(format_value(True), "true")
        self.assertEqual(format_value(False), "false")
        self.assertEqual(format_value(42), "42")
        self.assertEqual(format_value(-3), "-3")
        self.assertEqual(format_value(1.0), "1.0")
        self.assertEqual(format_value(0.25), "0.25")
        self.assertEqual(format_value("text"), '"text"')
        self.assertEqual(format_value(["a", "b"]), '["a", "b"]')

    def test_escaping(self) -> None:
        self.assertEqual(format_value('a"b'), '"a\\"b"')
        self.assertEqual(format_value("a\\b"), '"a\\\\b"')
        self.assertEqual(format_value("line\nbreak"), '"line\\nbreak"')

    def test_unsupported_type(self) -> None:
        with self.assertRaises(ConfigWriteError):
            format_value(object())


class TomlDocumentTests(unittest.TestCase):
    def test_set_existing_scalar(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        self.assertTrue(document.set_value(("app",), "tick_ms", 32))
        data = tomllib.loads(document.text())
        self.assertEqual(data["app"]["tick_ms"], 32)
        self.assertEqual(data["app"]["name"], "x")
        self.assertIn("# inline comment", document.text())

    def test_set_missing_key_appends_inside_section(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        self.assertTrue(document.set_value(("layout",), "hidden", True))
        text = document.text()
        data = tomllib.loads(text)
        self.assertTrue(data["layout"]["hidden"])
        self.assertTrue(
            text.index("hidden = true") < text.index("[groups.demo]"),
            "the new key must stay inside [layout]",
        )
        self.assertIn("# auto or manual", text)

    def test_set_value_creates_missing_section(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        document.set_value(("debug",), "enabled", True)
        data = tomllib.loads(document.text())
        self.assertTrue(data["debug"]["enabled"])

    def test_replace_multiline_array(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        document.set_inline_table_array(
            ("groups", "demo"),
            "rectangles",
            [
                {"name": "a", "x": 1, "y": 2, "width": 3, "height": 4},
                {"name": "b", "x": 5, "y": 6, "width": 7, "height": 8},
            ],
        )
        text = document.text()
        data = tomllib.loads(text)
        self.assertEqual([shape["name"] for shape in data["groups"]["demo"]["rectangles"]], ["a", "b"])
        self.assertNotIn("a comment inside the array", text)
        self.assertIn("# top comment", text)

    def test_empty_array(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        document.set_inline_table_array(
            ("groups", "demo"), "rectangles", [], empty_comment="nothing yet"
        )
        data = tomllib.loads(document.text())
        self.assertEqual(data["groups"]["demo"]["rectangles"], [])
        self.assertIn("# nothing yet", document.text())

    def test_set_values_if_missing_does_not_overwrite(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        changed = document.set_values_if_missing(
            ("groups", "demo"), {"resolution": "2560x1440", "author": "someone"}
        )
        self.assertTrue(changed)
        data = tomllib.loads(document.text())
        self.assertEqual(data["groups"]["demo"]["resolution"], "1920x1080")
        self.assertEqual(data["groups"]["demo"]["author"], "someone")

    def test_same_key_name_in_two_sections(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        document.set_value(("features", "toolbar"), "enabled", False)
        data = tomllib.loads(document.text())
        self.assertFalse(data["features"]["toolbar"]["enabled"])
        self.assertEqual(data["app"]["name"], "x")

    def test_comments_with_brackets_are_ignored(self) -> None:
        text = """\
[layout]
mode = "auto"   # [not a section]
note = "value [with] brackets"

[app]
name = "x"
"""
        document = TomlDocument.from_text(text)
        document.set_value(("layout",), "hidden", True)
        data = tomllib.loads(document.text())
        self.assertEqual(data["layout"]["mode"], "auto")
        self.assertTrue(data["layout"]["hidden"])
        self.assertEqual(data["app"]["name"], "x")

    def test_strings_containing_brackets(self) -> None:
        text = """\
[layout]
note = "a [ b ] c"
mode = "auto"
"""
        document = TomlDocument.from_text(text)
        document.set_value(("layout",), "mode", "manual")
        data = tomllib.loads(document.text())
        self.assertEqual(data["layout"]["note"], "a [ b ] c")
        self.assertEqual(data["layout"]["mode"], "manual")

    def test_create_section(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        self.assertTrue(
            document.create_section(("groups", "extra"), {"resolution": "2560x1440"})
        )
        self.assertFalse(
            document.create_section(("groups", "extra"), {"resolution": "1x1"})
        )
        data = tomllib.loads(document.text())
        self.assertEqual(data["groups"]["extra"]["resolution"], "2560x1440")

    def test_quoted_section_names(self) -> None:
        document = TomlDocument.from_text(SAMPLE)
        document.set_value(("groups", "with space"), "resolution", "1920x1080")
        data = tomllib.loads(document.text())
        self.assertEqual(data["groups"]["with space"]["resolution"], "1920x1080")
        # Reading it back through the editor must find the same section.
        self.assertTrue(document.has_section(("groups", "with space")))

    def test_array_of_tables_header_is_detected(self) -> None:
        text = """\
[[items]]
name = "a"

[app]
name = "x"
"""
        document = TomlDocument.from_text(text)
        document.set_value(("app",), "name", "y")
        data = tomllib.loads(document.text())
        self.assertEqual(data["items"][0]["name"], "a")
        self.assertEqual(data["app"]["name"], "y")


class TomlDocumentFileTests(unittest.TestCase):
    def _temp_path(self, text: str = SAMPLE) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="screenpainter-toml-"))
        path = directory / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_write_only_when_changed(self) -> None:
        path = self._temp_path()
        document = TomlDocument.from_path(path)
        self.assertFalse(document.write(path))
        mtime = path.stat().st_mtime_ns
        self.assertFalse(document.write(path))
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_write_round_trip(self) -> None:
        path = self._temp_path()
        document = TomlDocument.from_path(path)
        document.set_value(("app",), "tick_ms", 32)
        self.assertTrue(document.write(path))
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["app"]["tick_ms"], 32)

    def test_bom_is_removed(self) -> None:
        path = self._temp_path("\ufeff" + SAMPLE)
        document = TomlDocument.from_path(path)
        document.set_value(("app",), "tick_ms", 32)
        document.write(path)
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        tomllib.loads(raw.decode("utf-8"))

    def test_missing_file_raises(self) -> None:
        path = Path(tempfile.gettempdir()) / "screenpainter-not-here.toml"
        with self.assertRaises(ConfigWriteError):
            TomlDocument.from_path(path)


if __name__ == "__main__":
    unittest.main()
