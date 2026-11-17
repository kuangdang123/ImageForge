"""视频转换引擎的单元测试。

运行方式（仓库根目录）：
    & "D:\\Application\\Python\\python.exe" -m unittest tests.test_video -v
或：
    python -m unittest discover -s tests -p "test_video.py"

依赖本机 ffmpeg（winget install Gyan.FFmpeg.Essentials）；没有 ffmpeg 时全部用例跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imgforge import video  # noqa: E402

FFMPEG = video.find_ffmpeg()
FFPROBE = video.find_ffprobe()
HAS_FFMPEG = FFMPEG is not None
SKIP_REASON = "本机未检测到 ffmpeg，跳过视频转换测试"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(part) for part in cmd],
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@unittest.skipUnless(HAS_FFMPEG, SKIP_REASON)
class VideoTest(unittest.TestCase):
    """视频转换：探测 / 格式互转 / GIF / 命名规则 / 批量与错误。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imgforge-video-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._sample: Path | None = None

    # ---------------------------------------------------------- 测试素材 --
    def sample(self) -> Path:
        """2 秒 160×120@10fps 带音轨的 testsrc 视频。"""
        if self._sample is not None:
            return self._sample
        path = self.root / "sample.mp4"
        done = _run([
            FFMPEG, "-hide_banner", "-nostdin", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ])
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertTrue(path.is_file())
        self._sample = path
        return path

    def out_dir(self) -> Path:
        path = self.root / "out"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def options(self, target: str, **kwargs) -> video.VideoOptions:
        return video.VideoOptions(target=target, **kwargs)

    def convert(self, src: Path, target: str, **kwargs) -> video.VideoResult:
        return video.convert_video(src, self.options(target, **kwargs), self.out_dir())

    # -------------------------------------------------------------- 探测 --
    def test_probe_size_duration_codec(self) -> None:
        info = video.probe_video(self.sample())
        self.assertTrue(info.ok, info.message)
        self.assertEqual((info.width, info.height), (160, 120))
        self.assertAlmostEqual(info.duration, 2.0, delta=0.6)
        self.assertEqual(info.fps, 10.0)
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual(info.audio_codec, "aac")
        self.assertGreater(info.bytes, 0)
        self.assertEqual(info.format, "MP4")

    def test_probe_missing_file(self) -> None:
        info = video.probe_video(self.root / "nope.mp4")
        self.assertFalse(info.ok)
        self.assertEqual(info.message, "文件不存在")

    def test_probe_accepts_str_path(self) -> None:
        info = video.probe_video(str(self.sample()))
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.width, 160)

    def test_probe_without_ffprobe_falls_back_to_ffmpeg(self) -> None:
        """没有 ffprobe 时应回退到 `ffmpeg -i` 的 stderr 解析。"""
        if FFPROBE is None:
            self.skipTest("本机没有 ffprobe")
        with mock.patch.object(video, "find_ffprobe", return_value=None):
            info = video.probe_video(self.sample())
        self.assertTrue(info.ok, info.message)
        self.assertEqual((info.width, info.height), (160, 120))
        self.assertAlmostEqual(info.duration, 2.0, delta=0.6)
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual(info.audio_codec, "aac")

    def test_probe_gif_and_video_are_readable(self) -> None:
        gif = self.convert(self.sample(), "gif", resolution="720p")
        self.assertTrue(gif.ok, gif.message)
        info = video.probe_video(gif.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.width, 160)

    # ---------------------------------------------------------- 格式互转 --
    def test_convert_mp4_to_webm(self) -> None:
        result = self.convert(self.sample(), "webm", quality=50)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.dest.is_file())
        self.assertGreater(result.dest.stat().st_size, 0)
        self.assertEqual(result.dest.suffix, ".webm")
        self.assertEqual(result.dest.name, "sample.webm")
        info = video.probe_video(result.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.video_codec, "vp9")
        self.assertEqual((info.width, info.height), (160, 120))

    def test_convert_mp4_to_mkv(self) -> None:
        result = self.convert(self.sample(), "mkv")
        self.assertTrue(result.ok, result.message)
        self.assertGreater(result.dest.stat().st_size, 0)
        info = video.probe_video(result.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual((info.width, info.height), (160, 120))

    def test_convert_mp4_to_avi(self) -> None:
        result = self.convert(self.sample(), "avi", quality=80)
        self.assertTrue(result.ok, result.message)
        self.assertGreater(result.dest.stat().st_size, 0)
        info = video.probe_video(result.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.video_codec, "mpeg4")
        self.assertEqual((info.width, info.height), (160, 120))

    def test_convert_mov(self) -> None:
        result = self.convert(self.sample(), "mov")
        self.assertTrue(result.ok, result.message)
        self.assertGreater(result.dest.stat().st_size, 0)
        self.assertTrue(video.probe_video(result.dest).ok)

    def test_convert_to_same_format_keeps_source(self) -> None:
        """输出到源目录、目标与源同名时必须改名，绝不覆盖源视频。"""
        src = self.sample()
        before = src.read_bytes()
        result = video.convert_video(src, self.options("mp4"))  # out_root=None → 源目录
        self.assertTrue(result.ok, result.message)
        self.assertTrue(src.is_file())
        self.assertEqual(src.read_bytes(), before, "源文件被改写了")
        self.assertNotEqual(result.dest, src)
        self.assertEqual(result.dest.name, "sample_mp4.mp4")
        self.assertGreater(result.dest.stat().st_size, 0)
        self.assertTrue(video.probe_video(result.dest).ok)

    def test_audio_disabled(self) -> None:
        result = self.convert(self.sample(), "mp4", audio=False)
        self.assertTrue(result.ok, result.message)
        info = video.probe_video(result.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual(info.audio_codec, "")

    def test_resolution_not_upscaled(self) -> None:
        result = self.convert(self.sample(), "mp4", resolution="720p")
        self.assertTrue(result.ok, result.message)
        info = video.probe_video(result.dest)
        self.assertTrue(info.ok, info.message)
        self.assertEqual((info.width, info.height), (160, 120))

    def test_convert_result_reports_metadata(self) -> None:
        result = self.convert(self.sample(), "mp4")
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.width, result.height), (160, 120))
        self.assertAlmostEqual(result.duration, 2.0, delta=0.6)
        self.assertGreater(result.bytes, 0)
        self.assertGreater(result.seconds, 0.0)
        self.assertEqual(result.message, "完成")

    # ---------------------------------------------------------------- GIF --
    def test_convert_mp4_to_gif(self) -> None:
        result = self.convert(self.sample(), "gif")
        self.assertTrue(result.ok, result.message)
        self.assertGreater(result.dest.stat().st_size, 0)
        self.assertEqual(result.dest.suffix, ".gif")
        from PIL import Image

        with Image.open(result.dest) as img:
            self.assertEqual(img.format, "GIF")
            self.assertEqual(img.size[0], 160)
            img.seek(0)
            self.assertEqual(img.mode, "P")

    def test_gif_fps_takes_effect(self) -> None:
        """源是 10fps：fps=5 的帧数应明显少于 fps=10。"""
        from PIL import Image

        def frames(**kwargs) -> int:
            result = self.convert(self.sample(), "gif", **kwargs)
            self.assertTrue(result.ok, result.message)
            with Image.open(result.dest) as img:
                return getattr(img, "n_frames", 1)

        slow = frames(fps=5)
        fast = frames(fps=10)
        self.assertGreater(fast, slow)
        self.assertGreaterEqual(slow, 6)   # 2 秒 @5fps ≈ 10 帧，留余量
        self.assertLessEqual(fast, 24)     # 2 秒 @10fps ≈ 20 帧

    def test_gif_loop_option(self) -> None:
        loop = self.convert(self.sample(), "gif")
        once = self.convert(self.sample(), "gif", loop=False)
        self.assertTrue(loop.ok, loop.message)
        self.assertTrue(once.ok, once.message)
        from PIL import Image

        with Image.open(loop.dest) as img:
            self.assertEqual(img.info.get("loop"), 0)
        with Image.open(once.dest) as img:
            self.assertNotEqual(img.info.get("loop"), 0)

    def test_gif_ignores_audio_and_warns(self) -> None:
        result = self.convert(self.sample(), "gif", audio=True)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(any("音轨" in item for item in result.warnings), result.warnings)
        self.assertEqual(video.probe_video(result.dest).audio_codec, "")

    def test_gif_clip_range(self) -> None:
        result = self.convert(self.sample(), "gif", start=0.5, duration=0.5, fps=10)
        self.assertTrue(result.ok, result.message)
        from PIL import Image

        with Image.open(result.dest) as img:
            self.assertLessEqual(getattr(img, "n_frames", 1), 12)

    # ---------------------------------------------------------- 命名规则 --
    def test_duplicate_name_increments(self) -> None:
        source = self.sample().with_name("clip.mkv")
        shutil.copy(self.sample(), source)
        first = self.convert(source, "mp4")
        second = self.convert(source, "mp4")
        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(first.dest.name, "clip.mp4")
        self.assertEqual(second.dest.name, "clip (2).mp4")
        self.assertNotEqual(first.dest, second.dest)

    def test_existing_file_is_never_overwritten(self) -> None:
        source = self.sample().with_name("clip.mkv")
        shutil.copy(self.sample(), source)
        existing = self.out_dir() / "clip.mp4"
        existing.write_bytes(b"keep-me")
        result = self.convert(source, "mp4")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(existing.read_bytes(), b"keep-me")
        self.assertEqual(result.dest.name, "clip (2).mp4")

    def test_overwrite_flag_replaces_existing_file(self) -> None:
        source = self.sample().with_name("clip.mkv")
        shutil.copy(self.sample(), source)
        existing = self.out_dir() / "clip.mp4"
        existing.write_bytes(b"old")
        result = self.convert(source, "mp4", overwrite=True)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.dest, existing)
        self.assertGreater(existing.stat().st_size, len(b"old"))

    def test_suffix_option(self) -> None:
        result = self.convert(self.sample(), "mp4", suffix="_small")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.dest.name, "sample_small.mp4")

    def test_no_part_file_left_behind(self) -> None:
        result = self.convert(self.sample(), "mkv")
        self.assertTrue(result.ok, result.message)
        leftovers = list(self.out_dir().glob("*.part"))
        self.assertEqual(leftovers, [])

    # ---------------------------------------------------------- 批量与错误 --
    def test_run_batch_converts_all(self) -> None:
        first = self.sample().with_name("a.mp4")
        second = self.sample().with_name("b.mp4")
        shutil.copy(self.sample(), first)
        shutil.copy(self.sample(), second)
        seen: list[tuple[int, str]] = []

        def on_result(index: int, result: video.VideoResult) -> None:
            seen.append((index, result.dest.name if result.dest else ""))

        results = video.run_batch(
            [first, second], self.options("mkv"), self.out_dir(), on_result=on_result
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.ok for item in results), [item.message for item in results])
        self.assertEqual(seen, [(0, "a.mkv"), (1, "b.mkv")])

    def test_run_batch_cancel(self) -> None:
        paths = [self.sample(), self.sample().with_name("b.mp4")]
        shutil.copy(self.sample(), paths[1])
        results = video.run_batch(
            paths, self.options("mkv"), self.out_dir(), should_cancel=lambda: True
        )
        self.assertEqual(len(results), 2)
        for item in results:
            self.assertFalse(item.ok)
            self.assertEqual(item.message, "已取消")
        self.assertEqual(list(self.out_dir().glob("*.mkv")), [])

    def test_run_batch_skips_bad_files(self) -> None:
        missing = self.root / "missing.mp4"
        results = video.run_batch([missing], self.options("mkv"), self.out_dir())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].message, "文件不存在")

    def test_missing_source(self) -> None:
        result = self.convert(self.root / "nope.mp4", "mkv")
        self.assertFalse(result.ok)
        self.assertIsNone(result.dest)
        self.assertEqual(result.message, "文件不存在")

    def test_unsupported_input_extension(self) -> None:
        path = self.root / "note.txt"
        path.write_text("not a video", encoding="utf-8")
        result = self.convert(path, "mp4")
        self.assertFalse(result.ok)
        self.assertIn("不支持读取", result.message)

    def test_unsupported_target(self) -> None:
        result = self.convert(self.sample(), "flv")
        self.assertFalse(result.ok)
        self.assertIn("不支持的输出格式", result.message)

    def test_ffmpeg_hint_when_missing(self) -> None:
        self.assertEqual(video.ffmpeg_hint(), "")
        self.assertTrue(video.ffmpeg_available())
        try:
            with mock.patch.object(video, "_search_tool", return_value=None):
                video.reset_tool_cache()
                self.assertFalse(video.ffmpeg_available())
                hint = video.ffmpeg_hint()
            self.assertIn("未检测到 ffmpeg", hint)
            self.assertIn("winget install Gyan.FFmpeg.Essentials", hint)
        finally:
            video.reset_tool_cache()

    def test_env_var_override_ffmpeg(self) -> None:
        old = os.environ.get("IMAGEFORGE_FFMPEG")
        os.environ["IMAGEFORGE_FFMPEG"] = str(FFMPEG)
        try:
            video.reset_tool_cache()
            self.assertEqual(video.find_ffmpeg(), FFMPEG)
        finally:
            if old is None:
                os.environ.pop("IMAGEFORGE_FFMPEG", None)
            else:
                os.environ["IMAGEFORGE_FFMPEG"] = old
            video.reset_tool_cache()

    # -------------------------------------------------------------- 收集 --
    def test_collect_videos_natural_order(self) -> None:
        for name in ("clip10.mp4", "clip2.AVI", "clip1.mkv", "note.txt"):
            (self.root / name).write_bytes(b"x")
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "clip3.mp4").write_bytes(b"x")
        found = video.collect_videos(self.root)
        names = [path.name for path in found]
        # 根目录三个：clip1 < clip2 < clip10（natural 排序，不是字典序）
        self.assertEqual(names[:3], ["clip1.mkv", "clip2.AVI", "clip10.mp4"])
        self.assertIn("clip3.mp4", names)
        self.assertNotIn("note.txt", names)
        self.assertEqual(len(found), 4)
        # 不递归时只剩根目录
        self.assertEqual(len(video.collect_videos(self.root, recursive=False)), 3)

    # -------------------------------------------------------------- 缩略图 --
    def test_make_thumbnail(self) -> None:
        from PIL import Image

        image = video.make_thumbnail(self.sample(), size=80)
        self.assertIsNotNone(image)
        self.assertIsInstance(image, Image.Image)
        self.assertLessEqual(image.width, 80)
        self.assertLessEqual(image.height, 80)
        self.assertGreater(image.width, 0)

    def test_make_thumbnail_missing_file(self) -> None:
        self.assertIsNone(video.make_thumbnail(self.root / "nope.mp4"))

    # -------------------------------------------------------------- 参数 --
    def test_quality_mapping(self) -> None:
        self.assertEqual(video.quality_to_crf(100), 18)
        self.assertEqual(video.quality_to_crf(75), 18)   # round(51-33)=18
        self.assertEqual(video.quality_to_crf(50), 29)   # round(51-22)=29
        self.assertEqual(video.quality_to_crf(1), 40)    # 夹到上限
        self.assertEqual(video.quality_to_qscale(100), 3)   # round(31-28)=3
        self.assertEqual(video.quality_to_qscale(50), 17)   # round(31-14)=17
        self.assertEqual(video.quality_to_qscale(1), 31)

    def test_format_registry(self) -> None:
        self.assertEqual(video.VIDEO_FORMAT_ORDER, ["mp4", "mkv", "mov", "webm", "avi", "gif"])
        self.assertEqual(list(video.VIDEO_FORMATS), video.VIDEO_FORMAT_ORDER)
        for key in video.VIDEO_FORMAT_ORDER:
            spec = video.VIDEO_FORMATS[key]
            self.assertEqual(spec.key, key)
            self.assertEqual(spec.ext, f".{key}")
            self.assertIn(spec.kind, ("video", "gif"))
            self.assertTrue(1 <= spec.default_quality <= 100)
            self.assertTrue(spec.note)
        self.assertEqual(video.VIDEO_FORMATS["gif"].kind, "gif")
        self.assertEqual(video.VIDEO_FORMATS["webm"].video_codec, "libvpx-vp9")
        self.assertIn(".mp4", video.VIDEO_INPUT_EXTS)
        self.assertIn(".m2ts", video.VIDEO_INPUT_EXTS)
        self.assertNotIn(".txt", video.VIDEO_INPUT_EXTS)

    def test_command_has_no_shell_metacharacters(self) -> None:
        """命令必须是 list 形式：带空格 / 中文的路径不能被拆开。"""
        weird = self.root / "文件夹 with space.mkv"
        shutil.copy(self.sample(), weird)
        result = video.convert_video(weird, self.options("mp4"), self.out_dir())
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.dest.name, "文件夹 with space.mp4")
        self.assertTrue(video.probe_video(result.dest).ok)


if __name__ == "__main__":
    unittest.main()
