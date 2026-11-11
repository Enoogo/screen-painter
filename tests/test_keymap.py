"""Tests for the configurable keyboard shortcuts."""

from __future__ import annotations

import unittest

from screenpainter.core.keymap import (
    ACTIONS,
    DEFAULT_BINDINGS,
    KeyBinding,
    KeyBindingError,
    KeyMap,
    parse_binding,
)


class ParseBindingTests(unittest.TestCase):
    def test_named_key(self) -> None:
        binding = parse_binding("Delete")
        self.assertEqual(binding.key, "delete")
        self.assertFalse(binding.ctrl or binding.shift or binding.alt or binding.win)
        self.assertEqual(binding.describe(), "Delete")

    def test_letter_is_case_insensitive(self) -> None:
        binding = parse_binding("Ctrl+z")
        self.assertEqual(binding.key, "z")
        self.assertTrue(binding.ctrl)
        self.assertEqual(binding.describe(), "Ctrl+Z")

    def test_modifier_order_is_normalised(self) -> None:
        for text in ("Shift+Ctrl+Z", "ctrl+shift+z", "Ctrl + Shift + Z"):
            with self.subTest(text=text):
                self.assertEqual(parse_binding(text).describe(), "Ctrl+Shift+Z")

    def test_escape_aliases(self) -> None:
        self.assertEqual(parse_binding("Esc").key, "escape")
        self.assertEqual(parse_binding("Escape").describe(), "Esc")

    def test_arrow_keys(self) -> None:
        self.assertEqual(parse_binding("Left").key, "left")
        self.assertEqual(parse_binding("Shift+Left").describe(), "Shift+Left")
        self.assertNotEqual(parse_binding("Left"), parse_binding("Shift+Left"))

    def test_function_keys(self) -> None:
        self.assertEqual(parse_binding("F5").key, "f5")
        self.assertEqual(parse_binding("ctrl+F12").describe(), "Ctrl+F12")

    def test_tab_and_backspace(self) -> None:
        self.assertEqual(parse_binding("Tab").key, "tab")
        self.assertEqual(parse_binding("Backspace").key, "backspace")

    def test_plus_key(self) -> None:
        self.assertEqual(parse_binding("Ctrl++").key, "+")
        self.assertEqual(parse_binding("+").key, "+")

    def test_invalid_bindings(self) -> None:
        for text in ("", "   ", "Ctrl", "Ctrl+Shift", "Ctrl+Z+Q", "NoSuchKey", 5, None):
            with self.subTest(text=text):
                with self.assertRaises(KeyBindingError):
                    parse_binding(text)

    def test_modifiers_property(self) -> None:
        binding = parse_binding("Ctrl+Alt+Shift+Win+Z")
        self.assertEqual(binding.modifiers, frozenset({"ctrl", "alt", "shift", "win"}))


class KeyMapTests(unittest.TestCase):
    def test_defaults_cover_every_action(self) -> None:
        keys = KeyMap.default()
        self.assertEqual(set(keys.describe()), set(ACTIONS))
        self.assertEqual(keys.delete.describe(), "Delete")
        self.assertEqual(keys.undo.describe(), "Ctrl+Z")
        self.assertEqual(keys.redo.describe(), "Ctrl+Shift+Z")

    def test_partial_override_keeps_the_other_defaults(self) -> None:
        keys = KeyMap.from_mapping({"delete": "Backspace", "undo": "Ctrl+Alt+Z"})
        self.assertEqual(keys.delete.describe(), "Backspace")
        self.assertEqual(keys.undo.describe(), "Ctrl+Alt+Z")
        self.assertEqual(keys.redo.describe(), DEFAULT_BINDINGS["redo"])

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(KeyBindingError):
            KeyMap.from_mapping({"nope": "Delete"})

    def test_duplicate_bindings_are_rejected(self) -> None:
        with self.assertRaises(KeyBindingError):
            KeyMap.from_mapping({"undo": "Ctrl+Z", "redo": "Ctrl+Z"})

    def test_action_for(self) -> None:
        keys = KeyMap.default()
        self.assertEqual(keys.action_for(parse_binding("Delete")), "delete")
        self.assertIsNone(keys.action_for(parse_binding("Ctrl+F9")))

    def test_binding_lookup(self) -> None:
        keys = KeyMap.default()
        self.assertEqual(keys.binding("move_left_fast").describe(), "Shift+Left")
        with self.assertRaises(KeyError):
            keys.binding("nope")

    def test_binding_equality(self) -> None:
        self.assertEqual(KeyBinding("z", ctrl=True), KeyBinding("z", ctrl=True))
        self.assertNotEqual(KeyBinding("z", ctrl=True), KeyBinding("z", alt=True))


if __name__ == "__main__":
    unittest.main()
