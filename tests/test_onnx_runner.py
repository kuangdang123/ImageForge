"""ONNX 超分推理模块的测试：纯函数（provider/路径/瓦片规划/融合权重）+ 真实推理冒烟。

运行：
    python -m unittest tests.test_onnx_runner -v
    python -m unittest discover -s tests -v

集成用例需要 ``tmp/onnx_final/apisr/`` 下的已导出模型，并用 ``mock.patch`` 把
``onnx_runner.models_dir`` 指过去（不在仓库权重目录里）；模型或 onnxruntime
缺失时整类 skip。DML 不可用时自动走 CPU，不硬编码 GPU。
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from imgforge.upscale import onnx_runner  # noqa: E402
from imgforge.upscale.engine import UpscaleCancelled, weights_root  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ONNX_DIR = REPO / "tmp" / "onnx_final" / "apisr"
HAVE_RUNTIME = onnx_runner.runtime_available()
HAVE_GRL = (ONNX_DIR / "grl4.onnx").is_file()
HAVE_RRDB = (ONNX_DIR / "rrdb4.onnx").is_file()
HAS_DML = "DmlExecutionProvider" in onnx_runner.available_providers()


def _patch_models_dir() -> mock._patch:
    """把模型目录指向导出的临时目录，返回可用于 addCleanup/with 的 patcher。"""
    return mock.patch.object(onnx_runner, "models_dir", return_value=ONNX_DIR)


# --------------------------------------------------------------- provider --
class ProviderTest(unittest.TestCase):
    """provider 选择与后端描述；结果随本机是否装了 DirectML 变化。"""

    @unittest.skipUnless(HAVE_RUNTIME, "需要 onnxruntime")
    def test_available_providers_always_has_cpu(self) -> None:
        providers = onnx_runner.available_providers()
        self.assertIsInstance(providers, list)
        self.assertIn("CPUExecutionProvider", providers)
        self.assertEqual(providers, [str(item) for item in providers])

    def test_describe_backend_matches_available_providers(self) -> None:
        description = onnx_runner.describe_backend()
        self.assertIsInstance(description, str)
        self.assertTrue(description)
        if not HAVE_RUNTIME:
            self.assertEqual(description, "ONNX 运行时不可用")
        elif HAS_DML:
            self.assertEqual(description, "DirectML（GPU）")
        else:
            self.assertEqual(description, "CPU")

    def test_providers_for_cpu_is_cpu_only(self) -> None:
        self.assertEqual(onnx_runner.providers_for("cpu"), ["CPUExecutionProvider"])
        self.assertEqual(onnx_runner.providers_for("CPU"), ["CPUExecutionProvider"])

    def test_providers_for_auto_prefers_dml_then_cpu(self) -> None:
        providers = onnx_runner.providers_for("auto")
        self.assertIn("CPUExecutionProvider", providers)
        if HAS_DML:
            self.assertEqual(providers[0], "DmlExecutionProvider")
            self.assertEqual(providers, ["DmlExecutionProvider", "CPUExecutionProvider"])
        else:
            self.assertEqual(providers, ["CPUExecutionProvider"])

    def test_providers_for_gpu_degrades_without_dml(self) -> None:
        providers = onnx_runner.providers_for("gpu")
        self.assertIn("CPUExecutionProvider", providers)
        if HAS_DML:
            self.assertIn("DmlExecutionProvider", providers)
        else:
            self.assertNotIn("DmlExecutionProvider", providers)

    def test_providers_for_unknown_device_falls_back_to_auto(self) -> None:
        self.assertEqual(onnx_runner.providers_for("tpu"), onnx_runner.providers_for("auto"))
        self.assertEqual(onnx_runner.providers_for(""), onnx_runner.providers_for("auto"))

    def test_runtime_available_is_bool(self) -> None:
        self.assertIsInstance(onnx_runner.runtime_available(), bool)


# ------------------------------------------------------------------- 路径 --
class PathTest(unittest.TestCase):
    """模型目录 / 文件名 / 可用性检查。"""

    def test_constants(self) -> None:
        self.assertEqual(onnx_runner.ONNX_DIRNAME, "apisr")
        self.assertEqual(onnx_runner.TILE_OVERLAP, 32)
        self.assertEqual(onnx_runner.DEFAULT_TILE, 128)

    def test_models_dir_ends_with_apisr(self) -> None:
        directory = onnx_runner.models_dir()
        self.assertIsInstance(directory, Path)
        self.assertEqual(directory.name, "apisr")
        self.assertEqual(directory, weights_root() / onnx_runner.ONNX_DIRNAME)
        self.assertTrue(str(directory).endswith("apisr"))

    def test_model_path_joins_filename(self) -> None:
        path = onnx_runner.model_path("grl4.onnx")
        self.assertIsInstance(path, Path)
        self.assertEqual(path, onnx_runner.models_dir() / "grl4.onnx")
        self.assertEqual(path.name, "grl4.onnx")

    def test_models_ready_false_for_missing_dir(self) -> None:
        with mock.patch.object(onnx_runner, "models_dir", return_value=REPO / "tmp" / "no-such-dir"):
            self.assertFalse(onnx_runner.models_ready())

    def test_models_ready_false_without_onnx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "readme.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(onnx_runner, "models_dir", return_value=root):
                self.assertFalse(onnx_runner.models_ready())

    def test_models_ready_true_with_any_onnx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dummy.ONNX").write_bytes(b"not-a-real-model")
            with mock.patch.object(onnx_runner, "models_dir", return_value=root):
                self.assertTrue(onnx_runner.models_ready())


# --------------------------------------------------------------- 瓦片规划 --
class PlanTilesTest(unittest.TestCase):
    """``_plan_tiles`` 只做坐标规划，不依赖模型。"""

    def test_single_tile_for_small_image(self) -> None:
        self.assertEqual(onnx_runner._plan_tiles(128, 128, 128, 32), [(0, 0, 128, 128)])
        self.assertEqual(onnx_runner._plan_tiles(50, 37, 128, 32), [(0, 0, 50, 37)])

    def test_multiple_tiles_300x200(self) -> None:
        self.assertEqual(
            onnx_runner._plan_tiles(300, 200, 128, 32),
            [
                (0, 0, 128, 128),
                (96, 0, 224, 128),
                (192, 0, 300, 128),
                (0, 96, 128, 200),
                (96, 96, 224, 200),
                (192, 96, 300, 200),
            ],
        )

    def test_every_pixel_is_covered_and_inside_bounds(self) -> None:
        for width, height in ((1, 1), (50, 37), (128, 128), (129, 129), (300, 200), (513, 300)):
            with self.subTest(width=width, height=height):
                tiles = onnx_runner._plan_tiles(width, height, 128, 32)
                covered = np.zeros((height, width), dtype=bool)
                for x0, y0, x1, y1 in tiles:
                    self.assertIsInstance(x0, int)
                    self.assertLess(x0, x1)
                    self.assertLessEqual(x1, width)
                    self.assertLess(y0, y1)
                    self.assertLessEqual(y1, height)
                    covered[y0:y1, x0:x1] = True
                self.assertTrue(covered.all(), (width, height))

    def test_adjacent_tiles_overlap_by_requested_amount(self) -> None:
        tiles = onnx_runner._plan_tiles(300, 200, 128, 32)
        row = tiles[:3]
        # 第一列瓦片右边界与第二列左边界之间正好留出 32 像素重叠。
        self.assertEqual(row[0][2] - row[1][0], 32)
        self.assertEqual(row[1][2] - row[2][0], 32)
        top = tiles[0]
        second_row = tiles[3]
        self.assertEqual(top[3] - second_row[1], 32)
        # 相同原点不能重复出现。
        self.assertEqual(len(tiles), len(set(tiles)))

    def test_last_tile_is_clipped_to_image(self) -> None:
        tiles = onnx_runner._plan_tiles(300, 200, 128, 32)
        self.assertEqual(tiles[2], (192, 0, 300, 128))
        self.assertEqual(tiles[2][2], 300)
        self.assertEqual(tiles[-1][3], 200)

    def test_zero_overlap_uses_plain_grid(self) -> None:
        self.assertEqual(
            onnx_runner._plan_tiles(300, 128, 128, 0),
            [(0, 0, 128, 128), (128, 0, 256, 128), (256, 0, 300, 128)],
        )

    def test_overlap_is_clamped_to_half_tile(self) -> None:
        # 融合带最多取瓦片边长的一半，保证步长不退化成 1。
        tiles = onnx_runner._plan_tiles(300, 10, 128, 999)
        self.assertEqual(
            tiles,
            [(0, 0, 128, 10), (64, 0, 192, 10), (128, 0, 256, 10), (192, 0, 300, 10)],
        )

    def test_invalid_arguments_raise(self) -> None:
        for width, height, tile in ((0, 10, 128), (10, 0, 128), (10, 10, 0), (10, 10, -1)):
            with self.subTest(width=width, height=height, tile=tile):
                with self.assertRaises(ValueError):
                    onnx_runner._plan_tiles(width, height, tile, 32)


# --------------------------------------------------------------- 融合权重 --
class BlendWeightTest(unittest.TestCase):
    """``_blend_weight``：边缘小、中间 1、重叠处两块相加为 1。"""

    def test_shape_and_dtype(self) -> None:
        weight = onnx_runner._blend_weight(128, 32)
        self.assertEqual(weight.shape, (128,))
        self.assertEqual(weight.dtype, np.float32)

    def test_edges_small_and_center_one(self) -> None:
        weight = onnx_runner._blend_weight(128, 32)
        self.assertLess(float(weight[0]), 0.05)
        self.assertLess(float(weight[-1]), 0.05)
        self.assertEqual(float(weight.max()), 1.0)
        self.assertEqual(float(weight[32]), 1.0)
        self.assertEqual(float(weight[95]), 1.0)
        self.assertTrue(np.all(weight > 0.0))

    def test_ramp_is_monotonic(self) -> None:
        weight = onnx_runner._blend_weight(128, 32)
        # 左边缘单调升到 1、中间恒为 1、右边缘单调降回，左右镜像对称。
        self.assertTrue(np.all(np.diff(weight[:64]) >= 0.0))
        self.assertTrue(np.all(np.diff(weight[64:]) <= 0.0))
        self.assertTrue(np.allclose(weight, weight[::-1], atol=1e-7))
        self.assertLess(float(weight[0]), float(weight[16]))
        self.assertLess(float(weight[16]), float(weight[31]))

    def test_neighbouring_tiles_sum_to_one(self) -> None:
        weight = onnx_runner._blend_weight(128, 32)
        # 左瓦片尾部（局部 96..127）与右瓦片头部（局部 0..31）对应像素
        # 权重之和为 1，不会叠亮或叠暗。
        self.assertTrue(np.allclose(weight[96:128] + weight[0:32], 1.0, atol=1e-6))
        weight = onnx_runner._blend_weight(100, 16)
        self.assertTrue(np.allclose(weight[84:100] + weight[0:16], 1.0, atol=1e-6))

    def test_no_overlap_is_all_ones(self) -> None:
        self.assertTrue(np.all(onnx_runner._blend_weight(64, 0) == 1.0))
        self.assertTrue(np.all(onnx_runner._blend_weight(1, 0) == 1.0))

    def test_small_overlap_weights(self) -> None:
        self.assertTrue(np.allclose(onnx_runner._blend_weight(4, 1), [0.5, 1.0, 1.0, 0.5]))

    def test_invalid_size_raises(self) -> None:
        for size in (0, -3):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    onnx_runner._blend_weight(size, 32)


# ------------------------------------------------------------- 固定尺寸模型 --
@unittest.skipUnless(HAVE_RUNTIME and HAVE_GRL, "需要 onnxruntime 和 tmp/onnx_final/apisr/grl4.onnx")
class FixedInputModelTest(unittest.TestCase):
    """grl4：固定 128×128 输入 → 512×512，4× 放大。"""

    def setUp(self) -> None:
        patcher = _patch_models_dir()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rgba_alpha_is_upscaled_with_lanczos(self) -> None:
        image = Image.new("RGBA", (100, 80), (10, 200, 30, 0))
        image.paste(Image.new("RGBA", (60, 40), (200, 100, 50, 255)), (20, 20))
        warnings: list[str] = []
        started = time.perf_counter()
        result = onnx_runner.run_onnx(
            image,
            "grl4.onnx",
            scale=4,
            fixed_input=128,
            device="auto",
            warnings=warnings,
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 120.0)

        self.assertEqual(result.size, (400, 320))
        self.assertEqual(result.mode, "RGBA")
        alpha = np.asarray(result)[:, :, 3]
        # LANCZOS 单独放大的 alpha：四角透明、中间不透明，且不是全 0/全 255。
        for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
            self.assertLessEqual(int(alpha[y, x]), 8, (y, x, int(alpha[y, x])))
        self.assertGreaterEqual(int(alpha[160, 200]), 250)
        self.assertEqual(int(alpha.min()), 0)
        self.assertEqual(int(alpha.max()), 255)
        self.assertTrue(any("透明通道用 LANCZOS 放大" in item for item in warnings), warnings)
        # RGB 通道确实经过模型（不是原图直接放大）。
        rgb = np.asarray(result.convert("RGB"))
        self.assertFalse(np.array_equal(rgb, np.asarray(image.convert("RGB").resize(result.size, Image.Resampling.LANCZOS))))

    def test_multiple_tiles_report_progress(self) -> None:
        image = Image.new("RGB", (170, 110), (30, 60, 90))
        calls: list[tuple[str, float]] = []
        result = onnx_runner.run_onnx(
            image,
            "grl4.onnx",
            fixed_input=128,
            device="auto",
            progress=lambda message, fraction: calls.append((message, fraction)),
            warnings=None,
        )
        self.assertEqual(result.size, (680, 440))
        self.assertEqual(result.mode, "RGB")
        messages = [message for message, _ in calls]
        fractions = [fraction for _, fraction in calls]
        # 170×110 需要 2 个瓦片（横向 2 列、纵向 1 行）。
        self.assertIn("加载模型", messages)
        self.assertIn("第 1/2 块", messages)
        self.assertIn("第 2/2 块", messages)
        self.assertEqual(messages[-1], "推理完成")
        self.assertTrue(all(0.0 <= value <= 1.0 for value in fractions), fractions)
        self.assertAlmostEqual(fractions[-1], 1.0)
        self.assertEqual(fractions, sorted(fractions))

    def test_cancel_before_inference_raises(self) -> None:
        image = Image.new("RGB", (32, 32), (1, 2, 3))
        started = time.perf_counter()
        with self.assertRaisesRegex(UpscaleCancelled, "已取消"):
            onnx_runner.run_onnx(image, "grl4.onnx", fixed_input=128, cancel=lambda: True)
        # 取消在加载模型之前检查，不应等一次推理。
        self.assertLess(time.perf_counter() - started, 10.0)


# ------------------------------------------------------------- 动态尺寸模型 --
@unittest.skipUnless(HAVE_RUNTIME and HAVE_RRDB, "需要 onnxruntime 和 tmp/onnx_final/apisr/rrdb4.onnx")
class DynamicInputModelTest(unittest.TestCase):
    """rrdb4：动态 H/W，整图推理与强制瓦片都必须是 4×。"""

    def setUp(self) -> None:
        patcher = _patch_models_dir()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rgb_whole_image(self) -> None:
        image = Image.new("RGB", (96, 64), (30, 60, 90))
        warnings: list[str] = []
        result = onnx_runner.run_onnx(image, "rrdb4.onnx", device="auto", warnings=warnings)
        self.assertIsInstance(result, Image.Image)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (384, 256))
        pixels = np.asarray(result)
        self.assertEqual(pixels.dtype, np.uint8)
        self.assertGreater(int(pixels.std()), 1)  # 不是纯色/空白输出

    def test_forced_tile_matches_whole_image(self) -> None:
        image = Image.new("RGB", (96, 64), (120, 80, 40))
        whole = np.asarray(onnx_runner.run_onnx(image, "rrdb4.onnx")).astype(np.int16)
        # tile=64 < 96，强制走多块（重叠 32）融合路径。
        tiled = np.asarray(onnx_runner.run_onnx(image, "rrdb4.onnx", tile=64)).astype(np.int16)
        self.assertEqual(tiled.shape, (256, 384, 3))
        self.assertLess(float(np.abs(whole - tiled).mean()), 4.0)


# --------------------------------------------------------------- 会话缓存 --
@unittest.skipUnless(HAVE_RUNTIME and HAVE_RRDB, "需要 onnxruntime 和 tmp/onnx_final/apisr/rrdb4.onnx")
class SessionCacheTest(unittest.TestCase):
    """同一模型连续调用只建一次 InferenceSession。"""

    def setUp(self) -> None:
        patcher = _patch_models_dir()
        patcher.start()
        self.addCleanup(patcher.stop)
        self._drop_cached_sessions()

    @staticmethod
    def _cache_key() -> str:
        return str((ONNX_DIR / "rrdb4.onnx").resolve())

    def _drop_cached_sessions(self) -> None:
        with onnx_runner._SESSION_LOCK:
            stale = [key for key in onnx_runner._SESSION_CACHE if key[0] == self._cache_key()]
            for key in stale:
                del onnx_runner._SESSION_CACHE[key]

    def test_second_call_reuses_session(self) -> None:
        image = Image.new("RGB", (48, 32), (200, 10, 10))
        original = onnx_runner._create_session
        with mock.patch.object(onnx_runner, "_create_session", side_effect=original) as created:
            first = onnx_runner.run_onnx(image, "rrdb4.onnx")
            second = onnx_runner.run_onnx(image, "rrdb4.onnx")
        self.assertEqual(created.call_count, 1)
        self.assertEqual(first.size, (192, 128))
        self.assertEqual(second.size, (192, 128))
        self.assertTrue(np.array_equal(np.asarray(first), np.asarray(second)))
        with onnx_runner._SESSION_LOCK:
            entries = [key for key in onnx_runner._SESSION_CACHE if key[0] == self._cache_key()]
        self.assertEqual(len(entries), 1)


# ------------------------------------------------------------------- 错误 --
class RunOnnxErrorTest(unittest.TestCase):
    """中文错误提示，且不依赖模型文件。"""

    def test_non_image_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            onnx_runner.run_onnx("not-an-image", "grl4.onnx")  # type: ignore[arg-type]

    def test_runtime_unavailable_message(self) -> None:
        image = Image.new("RGB", (8, 8), (0, 0, 0))
        with mock.patch.object(onnx_runner, "runtime_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "ONNX 运行时不可用"):
                onnx_runner.run_onnx(image, "grl4.onnx")

    @unittest.skipUnless(HAVE_RUNTIME, "需要 onnxruntime")
    def test_missing_model_message(self) -> None:
        image = Image.new("RGB", (8, 8), (0, 0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(onnx_runner, "models_dir", return_value=Path(tmp)):
                with self.assertRaisesRegex(RuntimeError, "缺少模型文件：absent.onnx"):
                    onnx_runner.run_onnx(image, "absent.onnx")


if __name__ == "__main__":
    unittest.main()
