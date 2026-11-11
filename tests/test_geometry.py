"""Tests for geometry primitives."""

from __future__ import annotations

import unittest

from screenpainter.core.geometry import Rect


class RectTests(unittest.TestCase):
    def test_edges_and_size(self) -> None:
        rect = Rect(10, 20, 30, 40)
        self.assertEqual(rect.right, 40)
        self.assertEqual(rect.bottom, 60)
        self.assertEqual(rect.area, 1200)
        self.assertEqual(rect.size.width, 30)
        self.assertEqual(rect.size.height, 40)
        self.assertFalse(rect.is_empty)

    def test_intersection(self) -> None:
        first = Rect(0, 0, 100, 100)
        second = Rect(50, 60, 100, 20)
        self.assertEqual(first.intersection(second), Rect(50, 60, 50, 20))
        self.assertEqual(second.intersection(first), Rect(50, 60, 50, 20))
        self.assertIsNone(first.intersection(Rect(101, 0, 10, 10)))

    def test_no_intersection_at_touching_edge(self) -> None:
        self.assertFalse(Rect(0, 0, 10, 10).intersects(Rect(10, 0, 1, 1)))
        self.assertTrue(Rect(0, 0, 10, 10).intersects(Rect(9, 9, 2, 2)))

    def test_contains_and_translation(self) -> None:
        rect = Rect(10, 10, 5, 5)
        self.assertTrue(rect.contains_point(10, 10))
        self.assertTrue(rect.contains_point(14, 14))
        self.assertFalse(rect.contains_point(15, 14))
        self.assertEqual(rect.translated(5, -2), Rect(15, 8, 5, 5))

    def test_from_ltrb_rejects_negative_extent(self) -> None:
        self.assertEqual(Rect.from_ltrb(10, 20, 5, 0), Rect(10, 20, 0, 0))


if __name__ == "__main__":
    unittest.main()
