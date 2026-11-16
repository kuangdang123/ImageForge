"""settings.py 的单元测试：默认值合并、原子写、损坏回退、质量与尺寸归一化。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from imgforge.settings import DEFAULTS, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_falls_back_to_defaults(self) -> None:
        settings = Settings.load(self.path)
        self.assertEqual(settings["target"], DEFAULTS["target"])
        self.assertEqual(settings.sizes(), (16, 24, 32, 48, 64, 128, 256))

    def test_roundtrip_keeps_every_preference(self) -> None:
        settings = Settings.load(self.path)
        settings["target"] = "jpg"
        settings["ico_sizes"] = [16, 32, 256]
        settings["ico_fit"] = "fill"
        settings["ico_payload"] = "bmp"
        settings["ico_upscale"] = False
        settings["keep_metadata"] = False
        settings.set_quality("jpg", 88)
        settings["out_mode"] = "folder"
        settings["out_dir"] = r"D:\out"
        settings["keep_tree"] = False
        settings["recursive"] = False
        settings["theme"] = "dark"
        settings["window"] = "1200x800"
        settings["last_dir"] = r"D:\pics"
        self.assertTrue(settings.save())

        reloaded = Settings.load(self.path)
        self.assertEqual(reloaded["target"], "jpg")
        self.assertEqual(reloaded.sizes(), (16, 32, 256))
        self.assertEqual(reloaded["ico_fit"], "fill")
        self.assertEqual(reloaded["ico_payload"], "bmp")
        self.assertFalse(reloaded["ico_upscale"])
        self.assertFalse(reloaded["keep_metadata"])
        self.assertEqual(reloaded.quality("jpg", 92), 88)
        self.assertEqual(reloaded["out_mode"], "folder")
        self.assertEqual(reloaded["out_dir"], r"D:\out")
        self.assertFalse(reloaded["keep_tree"])
        self.assertFalse(reloaded["recursive"])
        self.assertEqual(reloaded["theme"], "dark")
        self.assertEqual(reloaded["window"], "1200x800")
        self.assertEqual(reloaded["last_dir"], r"D:\pics")

    def test_save_is_atomic_and_leaves_no_part_file(self) -> None:
        settings = Settings.load(self.path)
        settings["theme"] = "dark"
        settings.save()
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_suffix(".json.part").exists())

    def test_corrupt_json_falls_back_without_raising(self) -> None:
        self.path.write_text("{ this is not json", encoding="utf-8")
        settings = Settings.load(self.path)
        self.assertEqual(settings["target"], DEFAULTS["target"])

    def test_unknown_keys_are_not_persisted(self) -> None:
        settings = Settings(path=self.path)
        settings.update({"target": "png", "definitely_not_a_key": 1})
        settings.save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("definitely_not_a_key", raw)
        self.assertEqual(raw["target"], "png")

    def test_sizes_normalisation(self) -> None:
        settings = Settings(path=self.path)
        settings["ico_sizes"] = [256, 16, 16, "32", 999, 4]
        self.assertEqual(settings.sizes(), (16, 256))
        settings["ico_sizes"] = []
        self.assertEqual(settings.sizes(), (16, 24, 32, 48, 64, 128, 256))

    def test_quality_is_per_format(self) -> None:
        settings = Settings(path=self.path)
        settings.set_quality("jpg", 70)
        settings.set_quality("webp", 55)
        self.assertEqual(settings.quality("jpg", 92), 70)
        self.assertEqual(settings.quality("webp", 88), 55)
        self.assertEqual(settings.quality("avif", 62), 62)
        self.assertEqual(settings.quality("jpg", 92), 70)


if __name__ == "__main__":
    unittest.main()
