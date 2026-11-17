"""核心转换引擎与 ICO 容器的回归测试。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw, features  # noqa: E402

from imgforge import converter, ico  # noqa: E402
from imgforge.converter import ConvertOptions, ConvertResult, convert_file, probe  # noqa: E402


def make_alpha_png(path: Path, size=(300, 180)) -> Path:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, size[0] - 20, size[1] - 20], fill=(220, 40, 40, 255))
    draw.ellipse([60, 60, 140, 140], fill=(20, 120, 220, 255))
    img.save(path)
    return path


def make_photo_jpg(path: Path, size=(640, 480)) -> Path:
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = (x * 255 // size[0], y * 255 // size[1], 128)
    img.save(path, quality=90)
    return path


def make_animated_gif(path: Path, size=(80, 60), frames=3) -> Path:
    images = []
    for index in range(frames):
        frame = Image.new("RGB", size, (index * 70, 40, 200 - index * 60))
        ImageDraw.Draw(frame).text((10, 10), str(index), fill=(255, 255, 255))
        images.append(frame)
    images[0].save(path, save_all=True, append_images=images[1:], duration=120, loop=1)
    return path


class ConverterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imgforge-test-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def out_dir(self) -> Path:
        path = self.root / "out"
        path.mkdir(exist_ok=True)
        return path

    def options(self, target: str, **kwargs) -> ConvertOptions:
        return ConvertOptions(target=target, **kwargs)

    def convert(self, src: Path, target: str, **kwargs) -> ConvertResult:
        return convert_file(src, self.options(target, **kwargs), self.out_dir())

    # ------------------------------------------------------------ 探测 --
    def test_probe_reads_metadata(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        info = probe(src)
        self.assertTrue(info.ok)
        self.assertEqual((info.width, info.height), (300, 180))
        self.assertEqual(info.frames, 1)
        self.assertTrue(info.has_alpha)

    def test_probe_rejects_garbage(self) -> None:
        bad = self.root / "broken.png"
        bad.write_bytes(b"not really an image")
        info = probe(bad)
        self.assertFalse(info.ok)
        self.assertIn("无法识别", info.message)

    # -------------------------------------------------------------- ICO --
    def test_ico_contains_every_requested_size(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        result = self.convert(src, "ico", ico_sizes=(16, 32, 48, 256))
        self.assertTrue(result.ok, result.message)
        entries = ico.read_entries(result.dest)
        self.assertEqual([entry["size"][0] for entry in entries], [16, 32, 48, 256])
        self.assertTrue(all(entry["format"] == "png" for entry in entries))
        with Image.open(result.dest) as img:
            self.assertEqual(img.size, (256, 256))
            for size in ((16, 16), (256, 256)):
                img.size = size
                img.load()
                self.assertEqual(img.size, size)

    def test_ico_pads_non_square_with_transparency(self) -> None:
        src = self.root / "wide.png"
        Image.new("RGB", (400, 100), (10, 200, 10)).save(src)
        result = self.convert(src, "ico", ico_sizes=(64,), ico_fit="contain")
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            img.seek(0)
            frame = img.convert("RGBA")
        self.assertEqual(frame.size, (64, 64))
        self.assertEqual(frame.getpixel((32, 2))[3], 0, "上下留白应为透明")
        self.assertEqual(frame.getpixel((32, 32))[:3], (10, 200, 10))

    def test_ico_bmp_payload_is_readable(self) -> None:
        src = self.root / "bmp_payload.png"
        img = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 109, 109], fill=(220, 40, 40, 255))
        draw.rectangle([45, 45, 75, 75], fill=(20, 120, 220, 255))
        img.save(src)
        result = self.convert(src, "ico", ico_sizes=(64,), ico_payload="bmp")
        self.assertTrue(result.ok, result.message)
        entries = ico.read_entries(result.dest)
        self.assertEqual({entry["format"] for entry in entries}, {"bmp"})
        self.assertTrue(all(entry["bit_count"] == 32 for entry in entries))
        with Image.open(result.dest) as img:
            img.size = (64, 64)
            img.load()
            frame = img.convert("RGBA")
        self.assertEqual(frame.size, (64, 64))
        self.assertEqual(frame.getpixel((2, 2))[3], 0, "BMP 载荷必须保留透明（AND 掩码）")
        center = frame.getpixel((32, 32))
        self.assertEqual(center[3], 255)
        self.assertLess(abs(center[0] - 20) + abs(center[1] - 120) + abs(center[2] - 220), 24,
                        "BGRA 通道顺序应为蓝绿红")

    def test_ico_skips_larger_sizes_when_upscale_disabled(self) -> None:
        src = self.root / "small.png"
        Image.new("RGBA", (48, 48), (200, 30, 30, 255)).save(src)
        result = self.convert(src, "ico", ico_sizes=(16, 32, 128), ico_upscale=False)
        self.assertTrue(result.ok, result.message)
        sizes = [entry["size"][0] for entry in ico.read_entries(result.dest)]
        self.assertEqual(sizes, [16, 32])

    def test_ico_fails_cleanly_when_all_sizes_need_upscale(self) -> None:
        src = self.root / "tiny.png"
        Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(src)
        result = self.convert(src, "ico", ico_sizes=(128, 256), ico_upscale=False)
        self.assertFalse(result.ok)
        self.assertIn("放大", result.message)
        self.assertFalse(result.dest.exists(), "失败时不应留下半截文件")

    def test_ico_fill_mode_crops_instead_of_padding(self) -> None:
        src = self.root / "wide.png"
        Image.new("RGB", (400, 100), (10, 200, 10)).save(src)
        result = self.convert(src, "ico", ico_sizes=(32,), ico_fit="fill")
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            frame = img.convert("RGBA")
        self.assertEqual(frame.getpixel((16, 1))[3], 255)
        self.assertEqual(frame.getpixel((16, 16))[:3], (10, 200, 10))

    # ------------------------------------------------------------ 常规格式 --
    def test_jpg_flattens_alpha_onto_background(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        result = self.convert(src, "jpg", quality=90, background=(0, 0, 255))
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            self.assertEqual(img.mode, "RGB")
            # JPEG 有压缩误差，只检查色调接近纯蓝
            r, g, b = img.getpixel((2, 2))
        self.assertLess(r, 40)
        self.assertLess(g, 40)
        self.assertGreater(b, 200)

    def test_png_keeps_alpha(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        result = self.convert(src, "png")
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            self.assertEqual(img.mode, "RGBA")
            self.assertEqual(img.getpixel((2, 2))[3], 0)

    def test_webp_lossless_roundtrip_is_pixel_identical(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        result = self.convert(src, "webp", lossless=True)
        self.assertTrue(result.ok, result.message)
        with Image.open(src) as before, Image.open(result.dest) as after:
            self.assertEqual(before.convert("RGBA").size, after.convert("RGBA").size)
            diff = ImageChops.difference(before.convert("RGBA"), after.convert("RGBA"))
            self.assertIsNone(diff.getbbox(), "无损 WebP 应与原图一致")

    def test_gif_is_not_an_output_format(self) -> None:
        src = make_photo_jpg(self.root / "p.jpg", (120, 90))
        result = self.convert(src, "gif")
        self.assertFalse(result.ok)
        self.assertIn("不支持", result.message)
        self.assertIsNone(result.dest)

    def test_gif_input_is_still_supported(self) -> None:
        src = make_animated_gif(self.root / "still.gif")
        result = self.convert(src, "png")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.dest.suffix, ".png")

    def test_animated_gif_to_webp_keeps_frames(self) -> None:
        src = make_animated_gif(self.root / "anim.gif")
        result = self.convert(src, "webp", quality=80)
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            self.assertEqual(img.n_frames, 3)

    def test_animated_source_to_static_target_warns(self) -> None:
        src = make_animated_gif(self.root / "anim.gif")
        result = self.convert(src, "png")
        self.assertTrue(result.ok, result.message)
        self.assertTrue(any("第一帧" in warning for warning in result.warnings))

    def test_tiff_output(self) -> None:
        src = make_photo_jpg(self.root / "p.jpg", (64, 64))
        result = self.convert(src, "tiff")
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            self.assertEqual(img.format, "TIFF")
            self.assertEqual(img.size, (64, 64))

    @unittest.skipUnless(features.check("avif"), "当前 Pillow 未编译 AVIF 支持")
    def test_avif_output(self) -> None:
        src = make_photo_jpg(self.root / "p.jpg", (64, 64))
        result = self.convert(src, "avif", quality=60)
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as img:
            self.assertEqual(img.format, "AVIF")

    # ------------------------------------------------------------ 命名规则 --
    def test_never_overwrites_source_when_target_matches(self) -> None:
        src = make_alpha_png(self.root / "same.png")
        before = src.read_bytes()
        result = self.convert(src, "png")
        self.assertTrue(result.ok, result.message)
        self.assertNotEqual(result.dest, src)
        self.assertEqual(src.read_bytes(), before, "源文件必须原封不动")

    def test_conflict_gets_numbered_suffix(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        first = self.convert(src, "jpg")
        second = self.convert(src, "jpg")
        self.assertNotEqual(first.dest, second.dest)
        self.assertEqual(second.dest.name, "a (2).jpg")

    def test_overwrite_flag_reuses_path(self) -> None:
        src = make_alpha_png(self.root / "a.png")
        options = self.options("jpg", overwrite=True)
        first = convert_file(src, options, self.out_dir())
        second = convert_file(src, options, self.out_dir())
        self.assertEqual(first.dest, second.dest)

    def test_suffix_and_custom_stem(self) -> None:
        src = make_alpha_png(self.root / "logo.png")
        result = self.convert(src, "ico", suffix="_icon", ico_sizes=(32,))
        self.assertEqual(result.dest.name, "logo_icon.ico")

    # ------------------------------------------------------------ 批量与错误 --
    def test_batch_keeps_going_after_failure(self) -> None:
        good = make_alpha_png(self.root / "good.png")
        bad = self.root / "bad.png"
        bad.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        results = converter.run_batch([good, bad], self.options("jpg"), self.out_dir())
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertEqual(len(results), 2)

    def test_missing_file_reports_error(self) -> None:
        result = self.convert(self.root / "nope.png", "png")
        self.assertFalse(result.ok)
        self.assertIn("不存在", result.message)

    def test_unsupported_extension(self) -> None:
        src = self.root / "note.txt"
        src.write_text("hi", encoding="utf-8")
        result = self.convert(src, "png")
        self.assertFalse(result.ok)
        self.assertIn("不支持", result.message)

    def test_exif_orientation_is_applied(self) -> None:
        src = self.root / "rotated.jpg"
        img = Image.new("RGB", (120, 60), (200, 100, 50))
        exif = img.getexif()
        exif[0x0112] = 6  # 顺时针 90°
        img.save(src, exif=exif.tobytes())
        result = self.convert(src, "png")
        self.assertTrue(result.ok, result.message)
        with Image.open(result.dest) as out:
            self.assertEqual(out.size, (60, 120), "应按 EXIF 方向旋转")

    def test_human_size(self) -> None:
        self.assertEqual(converter.human_size(512), "512 B")
        self.assertEqual(converter.human_size(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main()