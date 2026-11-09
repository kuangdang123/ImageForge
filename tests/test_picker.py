"""文件夹浏览窗口里与界面无关的纯逻辑：自然排序与搜索匹配。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from imgforge.picker import matches, natural_key  # noqa: E402


class NaturalKeyTest(unittest.TestCase):
    def test_numbers_sort_numerically(self) -> None:
        names = ["img10.png", "img2.png", "img1.png"]
        self.assertEqual(
            sorted(names, key=natural_key),
            ["img1.png", "img2.png", "img10.png"],
        )

    def test_case_insensitive(self) -> None:
        self.assertEqual(natural_key("Photo.JPG"), natural_key("photo.jpg"))

    def test_digits_and_letters_are_ordered_by_kind(self) -> None:
        # 数字段排在文本段之前，避免 "1a" 与 "a1" 互相穿插
        self.assertLess(natural_key("1a"), natural_key("a1"))


class MatchesTest(unittest.TestCase):
    def test_empty_query_matches_everything(self) -> None:
        self.assertTrue(matches("anything.png", ""))
        self.assertTrue(matches("anything.png", "   "))

    def test_substring_case_insensitive(self) -> None:
        self.assertTrue(matches("Beach_Photo.JPG", "beach"))
        self.assertTrue(matches("Beach_Photo.JPG", "PHOTO"))

    def test_multiple_words_must_all_match(self) -> None:
        self.assertTrue(matches("beach photo 01.png", "beach 01"))
        self.assertFalse(matches("beach photo 01.png", "beach 02"))

    def test_extension_is_searchable(self) -> None:
        self.assertTrue(matches("logo_square.png", ".png"))
        self.assertFalse(matches("logo_square.png", ".jpg"))


if __name__ == "__main__":
    unittest.main()