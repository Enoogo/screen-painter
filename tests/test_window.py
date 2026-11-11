"""Tests for window profile matching semantics."""

from __future__ import annotations

import re
import unittest

from screenpainter.core.window import WindowProfile


def _profile(**kwargs: object) -> WindowProfile:
    return WindowProfile(name="test", **kwargs)  # type: ignore[arg-type]


class WindowProfileTests(unittest.TestCase):
    def test_process_title_and_class_are_anded(self) -> None:
        profile = _profile(
            process_names=("Weixin.exe",),
            title_patterns=(re.compile("^Weixin$"),),
            class_names=("Qt51514QWindowIcon",),
        )
        self.assertTrue(
            profile.matches_identity(
                title="Weixin",
                class_name="Qt51514QWindowIcon",
                process_name="Weixin.exe",
            )
        )
        self.assertFalse(
            profile.matches_identity(
                title="Photos and Videos",
                class_name="Qt51514QWindowIcon",
                process_name="Weixin.exe",
            )
        )
        self.assertFalse(
            profile.matches_identity(
                title="Weixin", class_name="Other", process_name="Weixin.exe"
            )
        )

    def test_values_in_one_group_are_or(self) -> None:
        profile = _profile(
            process_names=("Weixin.exe", "WeChat.exe"),
            title_patterns=(re.compile("^Weixin$"), re.compile("^WeChat$")),
        )
        self.assertTrue(
            profile.matches_identity(
                title="WeChat", class_name="X", process_name="wechat.exe"
            )
        )
        self.assertTrue(
            profile.matches_identity(
                title="Weixin", class_name="X", process_name="Weixin.EXE"
            )
        )
        self.assertFalse(
            profile.matches_identity(
                title="Other", class_name="X", process_name="Weixin.exe"
            )
        )

    def test_exclusions(self) -> None:
        profile = _profile(
            process_names=("app.exe",),
            exclude_title_patterns=(re.compile("Preview"),),
        )
        self.assertFalse(
            profile.matches_identity(
                title="Preview", class_name="X", process_name="app.exe"
            )
        )
        self.assertTrue(
            profile.matches_identity(
                title="Main", class_name="X", process_name="app.exe"
            )
        )

    def test_missing_process_does_not_match_process_group(self) -> None:
        profile = _profile(process_names=("app.exe",))
        self.assertFalse(
            profile.matches_identity(title="T", class_name="C", process_name=None)
        )

    def test_size_filter(self) -> None:
        profile = _profile(title_patterns=(re.compile("."),), min_width=100, min_height=80)
        self.assertTrue(profile.matches_size(100, 80))
        self.assertFalse(profile.matches_size(99, 80))
        self.assertFalse(profile.matches_size(100, 79))


if __name__ == "__main__":
    unittest.main()
