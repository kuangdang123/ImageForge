"""超分放大模块的回归测试（目标尺寸计算 + 模型目录 + 真实 ncnn 冒烟）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from imgforge.upscale import (  # noqa: E402
    DEFAULT_MODEL_ID,
    DENOISE_LABELS,
    MODELS,
    MODEL_BY_ID,
    PRESET_LABELS,
    PRESET_ORDER,
    PRESETS,
    UpscaleOptions,
    available_devices,
    available_scales,
    compute_target,
    denoise_levels,
    estimate_output_size,
    fit_inside,
    get_model,
    guard_output_size,
    models_sorted,
    ncnn_exe,
    preferred_model_scale,
    scale_passes,
    supported_denoise_levels,
    upscale_file,
    weights_root,
)

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "samples" / "tall.png"
PHOTO = REPO / "samples" / "photo.jpg"
EXE = ncnn_exe()
HAVE_EXE = EXE.is_file()
HAVE_SAMPLE = SAMPLE.is_file()
HAVE_PHOTO = PHOTO.is_file()
HAVE_WEIGHTS = (EXE.parent / "models-se").is_dir()


class TargetMathTest(unittest.TestCase):
    """纯计算：等比装框、目标尺寸换算、倍率选择。"""

    def test_fit_inside_landscape_limited_by_height(self) -> None:
        self.assertEqual(fit_inside(800, 600, 1920, 1080), (1440, 1080))

    def test_fit_inside_wide_image_limited_by_height(self) -> None:
        self.assertEqual(fit_inside(200, 520, 1920, 1080), (415, 1080))

    def test_fit_inside_never_exceeds_box(self) -> None:
        width, height = fit_inside(4000, 3000, 1000, 700)
        self.assertLessEqual(width, 1000)
        self.assertLessEqual(height, 700)

    def test_fit_inside_keeps_at_least_one_pixel(self) -> None:
        self.assertEqual(fit_inside(10000, 1, 100, 100), (100, 1))

    def test_fit_inside_invalid_input_falls_back_to_source(self) -> None:
        self.assertEqual(fit_inside(0, 0, 100, 100), (1, 1))

    def test_compute_target_preset(self) -> None:
        self.assertEqual(compute_target(200, 520, "1080p"), (415, 1080))
        self.assertEqual(compute_target(800, 600, "4k"), (2880, 2160))

    def test_compute_target_accepts_preset_case_and_space(self) -> None:
        self.assertEqual(compute_target(200, 520, " 1080P "), (415, 1080))

    def test_compute_target_custom_valid(self) -> None:
        self.assertEqual(compute_target(800, 600, "custom", 1920, 1080), (1440, 1080))

    def test_compute_target_custom_invalid_returns_none(self) -> None:
        self.assertIsNone(compute_target(800, 600, "custom", 0, 1080))
        self.assertIsNone(compute_target(800, 600, "custom", 1920, 0))
        self.assertIsNone(compute_target(800, 600, "custom"))

    def test_compute_target_unknown_preset_returns_none(self) -> None:
        self.assertIsNone(compute_target(800, 600, "16k"))
        self.assertIsNone(compute_target(800, 600, ""))

    def test_preferred_model_scale_min_scale_covering_needed(self) -> None:
        pro = get_model("cugan-pro")
        self.assertEqual(preferred_model_scale(pro, 1.2), 2)
        self.assertEqual(preferred_model_scale(pro, 2.0), 2)
        self.assertEqual(preferred_model_scale(pro, 2.5), 3)

    def test_preferred_model_scale_falls_back_to_largest(self) -> None:
        # pro 只有 2/3，需求 3.5 时没有满足项，退到最大倍率 3
        self.assertEqual(preferred_model_scale(get_model("cugan-pro"), 3.5), 3)
        self.assertEqual(preferred_model_scale(get_model("cugan-se"), 4.5), 4)
        self.assertEqual(preferred_model_scale(get_model("cugan-nose"), 0.5), 1)


class CatalogTest(unittest.TestCase):
    """模型目录：排序、默认值、标签完整性。"""

    def test_models_sorted_by_rank(self) -> None:
        ranks = [model.rank for model in models_sorted()]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks[0], 0)

    def test_models_sorted_covers_all_models(self) -> None:
        self.assertEqual({model.id for model in models_sorted()}, {model.id for model in MODELS})

    def test_default_model_exists(self) -> None:
        self.assertIn(DEFAULT_MODEL_ID, MODEL_BY_ID)
        self.assertEqual(get_model(DEFAULT_MODEL_ID).id, DEFAULT_MODEL_ID)

    def test_get_model_unknown_falls_back_to_default(self) -> None:
        self.assertEqual(get_model("nope").id, DEFAULT_MODEL_ID)
        self.assertEqual(get_model("").id, DEFAULT_MODEL_ID)

    def test_model_metadata_consistency(self) -> None:
        for model in MODELS:
            self.assertIn(model.default_scale, model.scales, model.id)
            self.assertIn(model.default_denoise, DENOISE_LABELS, model.id)
            if model.backend == "onnx":
                self.assertEqual(model.family, "APISR", model.id)
                self.assertTrue(model.onnx_file, model.id)
                self.assertEqual(model.pack, "apisr", model.id)
            else:
                self.assertEqual(model.family, "Real-CUGAN", model.id)
                self.assertEqual(model.backend, "ncnn", model.id)
                self.assertEqual(model.pack, "ncnn", model.id)
                self.assertFalse(model.onnx_file, model.id)
            self.assertTrue(model.description)

    @unittest.skipUnless(HAVE_WEIGHTS, "缺少模型权重目录，跳过")
    def test_denoise_levels_depend_on_scale(self) -> None:
        # pro 只有 conservative / no-denoise / denoise3x（没有 1/2 档）
        self.assertEqual(denoise_levels("cugan-pro", 2), (-1, 0, 3))
        self.assertEqual(denoise_levels("cugan-pro", 3), (-1, 0, 3))
        # se 的 2× 全档，3×/4× 只有 -1/0/3
        self.assertEqual(denoise_levels("cugan-se", 2), (-1, 0, 1, 2, 3))
        self.assertEqual(denoise_levels("cugan-se", 3), (-1, 0, 3))
        self.assertEqual(denoise_levels("cugan-se", 4), (-1, 0, 3))
        # nose 只带 no-denoise 权重，catalog 声明的 -1 不可用
        self.assertEqual(denoise_levels("cugan-nose", 2), (0,))

    @unittest.skipUnless(HAVE_WEIGHTS, "缺少模型权重目录，跳过")
    def test_denoise_levels_model_method_matches_function(self) -> None:
        for model in models_sorted():
            self.assertEqual(
                model.denoise_levels_for(model.default_scale),
                denoise_levels(model.id, model.default_scale),
            )
            self.assertEqual(
                supported_denoise_levels(model, model.default_scale),
                denoise_levels(model.id, model.default_scale),
            )

    def test_denoise_levels_unknown_model_falls_back_to_default(self) -> None:
        self.assertEqual(
            denoise_levels("nope", 2),
            denoise_levels(DEFAULT_MODEL_ID, 2),
        )

    def test_denoise_labels_cover_all_levels(self) -> None:
        for model in MODELS:
            for level in model.denoise_levels:
                self.assertIn(level, DENOISE_LABELS, model.id)

    def test_presets_and_labels(self) -> None:
        self.assertEqual(list(PRESETS), PRESET_ORDER)
        self.assertEqual([PRESET_LABELS[key] for key in PRESET_ORDER],
                         ["720p", "1080p", "2K", "4K", "8K"])
        for key, (width, height) in PRESETS.items():
            self.assertGreater(width, 0)
            self.assertLess(width, height * 2)
            self.assertIn(key, PRESET_LABELS)


class EstimateTest(unittest.TestCase):
    """UI 预估尺寸：倍数模式与目标尺寸模式。"""

    def test_estimate_scale_mode(self) -> None:
        options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=2)
        self.assertEqual(estimate_output_size(200, 520, options), (400, 1040))
        options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=4)
        self.assertEqual(estimate_output_size(200, 520, options), (800, 2080))

    def test_estimate_scale_mode_composes_unsupported_scale(self) -> None:
        # pro 单轮只有 2×/3×，4× 由多轮组合实现，最终尺寸仍是精确 4×
        options = UpscaleOptions(model_id="cugan-pro", mode="scale", scale=4)
        self.assertEqual(estimate_output_size(200, 520, options), (800, 2080))

    def test_estimate_scale_mode_one_x_keeps_size(self) -> None:
        # 1× 不调用模型，只做重新编码
        options = UpscaleOptions(model_id="cugan-nose", mode="scale", scale=1)
        self.assertEqual(estimate_output_size(200, 520, options), (200, 520))

    def test_estimate_target_mode(self) -> None:
        options = UpscaleOptions(mode="target", preset="1080p")
        self.assertEqual(estimate_output_size(200, 520, options), (415, 1080))

    def test_estimate_target_mode_source_already_big(self) -> None:
        options = UpscaleOptions(mode="target", preset="1080p")
        self.assertEqual(estimate_output_size(3000, 2000, options), (1620, 1080))

    def test_estimate_target_mode_custom(self) -> None:
        options = UpscaleOptions(mode="target", preset="custom", custom_width=1920, custom_height=1080)
        self.assertEqual(estimate_output_size(800, 600, options), (1440, 1080))

    def test_estimate_invalid_target_keeps_source(self) -> None:
        options = UpscaleOptions(mode="target", preset="custom")
        self.assertEqual(estimate_output_size(800, 600, options), (800, 600))


class EngineSupportTest(unittest.TestCase):
    """引擎周边：设备列表、路径、权重缺失时的中文报错。"""

    @unittest.skipUnless(HAVE_WEIGHTS, "缺少模型权重目录，跳过")
    def test_available_scales_follow_weights(self) -> None:
        # nose 磁盘上只有 up2x 权重（catalog 声明的 1× 不可用）
        self.assertEqual(available_scales("cugan-nose"), (2,))
        self.assertEqual(available_scales("cugan-pro"), (2, 3))
        self.assertEqual(available_scales("cugan-se"), (2, 3, 4))
        self.assertEqual(available_scales(get_model("cugan-se")), (2, 3, 4))

    def test_available_devices(self) -> None:
        devices = available_devices()
        self.assertEqual([key for key, _ in devices], ["auto", "gpu", "cpu"])
        self.assertTrue(all(label for _, label in devices))

    def test_weights_root_and_exe_paths(self) -> None:
        self.assertEqual(weights_root(), Path(REPO) / "imgforge" / "upscale" / "weights")
        self.assertEqual(ncnn_exe(), weights_root() / "ncnn" / "realcugan-ncnn-vulkan.exe")

    @unittest.skipUnless(HAVE_EXE, "未找到 realcugan-ncnn-vulkan.exe，跳过")
    def test_run_ncnn_missing_weights_raises_chinese_error(self) -> None:
        from imgforge.upscale import run_ncnn

        image = Image.new("RGB", (32, 32), (10, 20, 30))
        # pro 没有 up2x-denoise2x 权重，应在启动 exe 之前就给出中文 RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            run_ncnn(image, 2, get_model("cugan-pro"), 2, 0, False, "cpu")
        self.assertIn("缺少模型权重", str(ctx.exception))


@unittest.skipUnless(HAVE_EXE, "未找到 realcugan-ncnn-vulkan.exe，跳过")
@unittest.skipUnless(HAVE_SAMPLE, "未找到 samples/tall.png，跳过")
class EngineSmokeTest(unittest.TestCase):
    """真实推理冒烟：CPU 2× 放大 samples/tall.png（200×520 RGBA → 400×1040）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imgforge-upscale-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_upscale_cugan_se_2x_cpu(self) -> None:
        out_dir = self.root / "out"
        options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=2, device="cpu")
        started = time.perf_counter()
        result = upscale_file(SAMPLE, options, out_dir)
        elapsed = time.perf_counter() - started

        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.dest)
        self.assertTrue(result.dest.is_file())
        self.assertEqual((result.width, result.height), (400, 1040))
        self.assertEqual(result.model_scale, 2)
        self.assertEqual((result.src_width, result.src_height), (200, 520))
        self.assertGreater(result.seconds, 0.0)
        self.assertLess(result.seconds, 180.0, "单张 2× CPU 放大不应该超过 3 分钟")

        with Image.open(result.dest) as out_img:
            self.assertEqual(out_img.size, (400, 1040))
            self.assertEqual(out_img.mode, "RGBA")

        print(f"\n[upscale] 真实放大耗时 {result.seconds:.2f}s（wall {elapsed:.2f}s）→ {result.dest}")

    @unittest.skipUnless(HAVE_PHOTO, "未找到 samples/photo.jpg，跳过")
    def test_upscale_photo_jpg_cpu(self) -> None:
        # JPEG（RGB，无 alpha）走 CPU：验证不带透明通道的常见路径
        out_dir = self.root / "jpg"
        options = UpscaleOptions(model_id="cugan-nose", mode="scale", scale=2, device="cpu")
        result = upscale_file(PHOTO, options, out_dir)

        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.dest)
        self.assertEqual(result.dest.suffix.lower(), ".jpg")
        self.assertEqual((result.width, result.height), (1600, 1200))
        with Image.open(result.dest) as out_img:
            self.assertEqual(out_img.size, (1600, 1200))
            self.assertEqual(out_img.mode, "RGB")
        print(f"\n[upscale] photo.jpg CPU 耗时 {result.seconds:.2f}s → {result.dest}")

    def test_upscale_nose_never_overwrites_source(self) -> None:
        src = self.root / "tall.png"
        src.write_bytes(SAMPLE.read_bytes())
        original = src.read_bytes()

        # cugan-nose 只带 up2x-no-denoise 权重，默认 -1 应自动回退并给出警告
        options = UpscaleOptions(model_id="cugan-nose", mode="scale", scale=2, device="cpu")
        result = upscale_file(src, options)  # out_root=None → 源目录旁输出

        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.dest)
        self.assertNotEqual(result.dest.resolve(), src.resolve())
        self.assertEqual(result.dest.name, "tall_2x_cugan-nose.png")
        self.assertEqual(src.read_bytes(), original, "源文件不允许被改动")
        self.assertTrue(any("已改用" in warning for warning in result.warnings), result.warnings)
        self.assertEqual((result.width, result.height), (400, 1040))


@unittest.skipUnless(HAVE_SAMPLE, "未找到 samples/tall.png，跳过")
class CpuRetryTest(unittest.TestCase):
    """CPU 路径异常终止时自动退回 Vulkan 重试一次（不需要真实 exe）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="imgforge-retry-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def options(self) -> UpscaleOptions:
        return UpscaleOptions(model_id="cugan-se", mode="scale", scale=2, device="cpu")

    def test_cpu_crash_retries_with_gpu(self) -> None:
        import imgforge.upscale.engine as engine

        calls: list[str] = []

        def fake_run(image, scale, model, denoise, tile, tta, device, progress=None, cancel=None):
            calls.append(device)
            if len(calls) == 1:
                raise RuntimeError("ncnn 推理失败（返回码 -1073741819）：Access violation")
            return image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)

        with mock.patch.object(engine, "run_ncnn", side_effect=fake_run):
            result = upscale_file(SAMPLE, self.options(), self.root / "out")

        self.assertTrue(result.ok, result.message)
        self.assertEqual(calls, ["cpu", "auto"])
        self.assertIn("CPU 模式不可用，已改用 GPU", result.warnings)
        self.assertEqual((result.width, result.height), (400, 1040))

    def test_non_crash_error_does_not_retry(self) -> None:
        import imgforge.upscale.engine as engine

        calls: list[str] = []

        def fake_run(image, scale, model, denoise, tile, tta, device, progress=None, cancel=None):
            calls.append(device)
            raise RuntimeError("ncnn 推理失败（返回码 1）：boom")

        with mock.patch.object(engine, "run_ncnn", side_effect=fake_run):
            result = upscale_file(SAMPLE, self.options(), self.root / "out")

        self.assertFalse(result.ok)
        self.assertEqual(calls, ["cpu"])
        self.assertIn("推理失败", result.message)
        self.assertNotIn("CPU 模式不可用，已改用 GPU", result.warnings)


class MultiScaleTest(unittest.TestCase):
    """多轮组合放大（8×/16×/32×）与 1× 直通。"""

    def test_scale_choices_are_powers_of_two(self) -> None:
        from imgforge.upscale import SCALE_CHOICES

        for value in (1, 2, 4, 8, 16, 32):
            self.assertIn(value, SCALE_CHOICES)

    def test_scale_passes_single_when_model_supports_it(self) -> None:
        self.assertEqual(scale_passes("cugan-se", 4), (4,))
        self.assertEqual(scale_passes("cugan-pro", 3), (3,))
        self.assertEqual(scale_passes("cugan-se", 2), (2,))

    def test_scale_passes_compose_large_factors(self) -> None:
        for model_id in ("cugan-se", "cugan-pro"):
            for wanted in (8, 16, 32):
                passes = scale_passes(model_id, wanted)
                self.assertTrue(passes, f"{model_id} {wanted}")
                self.assertLessEqual(max(passes), 4, f"{model_id} {wanted} 单轮超过 4×")
                product = 1
                for scale in passes:
                    product *= scale
                self.assertGreaterEqual(product, wanted, f"{model_id} {wanted} 组合不够")

    def test_scale_passes_one_means_no_model(self) -> None:
        self.assertEqual(scale_passes("cugan-se", 1), ())
        self.assertEqual(scale_passes("cugan-se", 0), ())

    def test_guard_output_size_blocks_huge_results(self) -> None:
        guard_output_size(8000, 8000)
        with self.assertRaises(ValueError):
            guard_output_size(20000, 20000)
        with self.assertRaises(ValueError):
            guard_output_size(100, 40000)

    def test_estimate_scale_mode_supports_composed_scale(self) -> None:
        options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=8)
        self.assertEqual(estimate_output_size(100, 50, options), (800, 400))
        options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=16)
        self.assertEqual(estimate_output_size(100, 50, options), (1600, 800))

    def test_upscale_one_x_skips_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "small.png"
            Image.new("RGBA", (32, 24), (10, 20, 30, 180)).save(source)
            options = UpscaleOptions(model_id="cugan-se", mode="scale", scale=1, output_format="png")
            result = upscale_file(source, options, root / "out")
            self.assertTrue(result.ok, result.message)
            self.assertTrue(result.dest and result.dest.exists())
            self.assertEqual((result.width, result.height), (32, 24))
            self.assertEqual(result.model_scale, 1)
            self.assertTrue(any("1×" in item for item in result.warnings), result.warnings)


if __name__ == "__main__":
    unittest.main()
