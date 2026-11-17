"""图片超分辨率放大引擎（Real-CUGAN ncnn）。

推理通过 vendored 的 ``realcugan-ncnn-vulkan.exe`` 子进程完成，只依赖
Pillow + 标准库（不引入 numpy / cv2 / torch）。引擎负责：读图、算倍率、
调用 exe、按目标尺寸收尾、决定输出路径、保存、错误翻译成中文。

实测注意点（勿改回）：
* exe 的 ``-m`` 是相对 exe 目录的模型文件夹，所以 ``cwd`` 必须是 exe 所在目录；
* exe 只接受 jpg/png/webp 输入，这里统一用临时 PNG 交换；
* 该 exe 的 CPU 路径（``-g -1``）推理 **RGBA 图会崩溃**（0xC0000005），
  因此 CPU 模式先把透明通道剥掉、跑 RGB，再用 LANCZOS 单独放大 alpha 合并；
* 同一模型不同倍率可用的降噪等级并不一致（例如 models-se 的 3× 只有
  conservative / no-denoise / denoise3x），这里会按实际权重文件回退；
* 模型缺权重时 exe 不是优雅报错而是直接崩溃，所以调用前先检查权是否存在。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageOps, UnidentifiedImageError

from .catalog import DENOISE_LABELS, ModelSpec, get_model
from .targets import compute_target, preferred_model_scale

__all__ = [
    "UpscaleOptions",
    "UpscaleResult",
    "UpscaleCancelled",
    "weights_root",
    "ncnn_exe",
    "models_ready",
    "models_location_hint",
    "default_models_root",
    "set_models_root",
    "run_ncnn",
    "upscale_file",
    "run_batch",
    "estimate_output_size",
    "available_devices",
]

#: exe 能直接读写的图片后缀（其余格式输出统一转 PNG）。
_NCNN_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

#: 输出格式 -> (扩展名, Pillow 保存格式)
_FORMAT_TABLE: dict[str, tuple[str, str]] = {
    "png": (".png", "png"),
    "jpg": (".jpg", "jpg"),
    "webp": (".webp", "webp"),
}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PARAM_RE = re.compile(
    r"^up(?P<scale>\d+)x-(?P<kind>conservative|no-denoise|denoise(?P<noise>\d+)x)\.param$",
    re.IGNORECASE,
)

#: ncnn 崩溃时 Windows 异常码在 Python 里是负的返回码（如 0xC0000005 → -1073741819）。
_CRASH_RE = re.compile(r"返回码 (-?\d+)")

#: UI 里的倍率选项。1× 只做格式转换不调用模型；超过模型单轮上限的倍率
#: 由多轮推理组合实现（例如 8× = 4× × 2×）。
SCALE_CHOICES: tuple[int, ...] = (1, 2, 3, 4, 8, 16, 32)

#: 输出尺寸护栏：极端倍率（如 32× 的大图）会直接吃满内存，提前拒绝。
MAX_OUTPUT_PIXELS = 250_000_000
MAX_OUTPUT_SIDE = 32768


@dataclass
class UpscaleOptions:
    model_id: str = "cugan-pro"
    device: str = "auto"        # "auto" | "gpu" | "cpu"；auto/gpu 都交给 Vulkan 自选，cpu 传 -g -1
    mode: str = "scale"         # "scale" 倍数 | "target" 目标尺寸
    scale: int = 2
    preset: str = "1080p"
    custom_width: int = 0
    custom_height: int = 0
    denoise: int | None = None  # None=用模型默认
    tta: bool = False
    tile: int = 0
    output_format: str = "keep" # keep/png/jpg/webp
    quality: int = 92
    suffix: str = ""
    overwrite: bool = False


@dataclass
class UpscaleResult:
    src: Path
    dest: Path | None = None
    ok: bool = False
    message: str = ""
    src_width: int = 0
    src_height: int = 0
    width: int = 0
    height: int = 0
    seconds: float = 0.0
    model_scale: int = 0
    warnings: list[str] = field(default_factory=list)


class UpscaleCancelled(Exception):
    """用户在推理过程中取消了任务。"""


# --------------------------------------------------------------- 路径 --
#: 模型目录的文件夹名（放在 exe 旁边即为便携模式）。
MODELS_DIRNAME = "models"

_models_root_override: Path | None = None


def set_models_root(path: str | Path | None) -> None:
    """由 GUI / CLI 指定模型目录（None 表示恢复自动查找）。"""
    global _models_root_override
    _models_root_override = Path(path) if path else None


def default_models_root() -> Path:
    """用户可写的默认模型目录：%LOCALAPPDATA%\\ImageForge\\models。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "ImageForge" / MODELS_DIRNAME


def _candidate_roots() -> list[Path]:
    """按优先级列出候选模型根目录（每个都直接包含 ncnn/）。

    显式指定（set_models_root / IMAGEFORGE_MODELS_DIR）时只认指定位置，
    不再回退，便于用户明确控制模型装在哪里。
    """
    if _models_root_override:
        return [_models_root_override]

    env = os.environ.get("IMAGEFORGE_MODELS_DIR")
    if env:
        return [Path(env)]

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / MODELS_DIRNAME)
    else:
        # 源码运行：仓库内自带权重，开箱即用，优先于已安装的模型。
        candidates.append(Path(__file__).resolve().parent / "weights")

    candidates.append(default_models_root())

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "imgforge" / "upscale" / "weights")
    return candidates


def weights_root() -> Path:
    """模型根目录（其下直接是 ncnn/）；找不到已安装的目录时返回首选安装位置。"""
    candidates = _candidate_roots()
    for root in candidates:
        if (root / "ncnn").is_dir() or (root / "apisr").is_dir():
            return root
    return candidates[0]


def models_ready() -> bool:
    """模型是否已安装（ncnn exe 与至少一组权重齐全）。"""
    root = weights_root()
    if not (root / "ncnn" / "realcugan-ncnn-vulkan.exe").is_file():
        return False
    return any((root / "ncnn").glob("models-*/up*x-*.param"))


def models_location_hint() -> str:
    """给用户看的安装位置提示。"""
    return str(weights_root())


def pack_ready(pack: str) -> bool:
    """某个模型包（ncnn / apisr）是否已安装且可用。"""
    if pack == "ncnn":
        return models_ready()
    if pack == "apisr":
        try:
            from . import onnx_runner  # 延迟导入

            return bool(onnx_runner.models_ready())
        except Exception:  # noqa: BLE001 - 运行库缺失时视为未安装
            return False
    return False


def model_ready(model: "ModelSpec | str") -> bool:
    """某个模型（或 model_id）所需的模型包是否就绪。"""
    spec = get_model(model) if isinstance(model, str) else model
    return pack_ready(spec.pack)


def ncnn_exe() -> Path:
    """vendored realcugan-ncnn-vulkan.exe 的路径。"""
    return weights_root() / "ncnn" / "realcugan-ncnn-vulkan.exe"


# ------------------------------------------------------- 模型可用性 --
@lru_cache(maxsize=16)
def _weight_table(model_dir: str) -> dict[int, tuple[int, ...]]:
    """扫描模型目录，返回 {倍率: (实际存在的降噪等级,)}；目录缺失时返回空。"""
    folder = weights_root() / "ncnn" / model_dir
    table: dict[int, set[int]] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return {}
    for entry in entries:
        if entry.suffix.lower() != ".param":
            continue
        match = _PARAM_RE.match(entry.name)
        if not match:
            continue
        scale = int(match.group("scale"))
        kind = match.group("kind").lower()
        if kind == "conservative":
            denoise = -1
        elif kind == "no-denoise":
            denoise = 0
        else:
            denoise = int(match.group("noise"))
        table.setdefault(scale, set()).add(denoise)
    return {scale: tuple(sorted(levels)) for scale, levels in table.items()}


def clear_model_cache() -> None:
    """模型目录变化（下载/切换）后清掉可用性缓存。"""
    _weight_table.cache_clear()


def _available_scales(model: ModelSpec) -> tuple[int, ...]:
    """模型在磁盘上真实可用的倍率（优先与 catalog 声明取交集）。"""
    if model.backend == "onnx":
        return tuple(sorted(model.scales))
    table = _weight_table(model.model_dir)
    if not table:
        return tuple(sorted(model.scales))
    supported = tuple(sorted(scale for scale in model.scales if scale in table))
    if supported:
        return supported
    return tuple(sorted(table))


def _preferred_scale(model: ModelSpec, needed: float) -> int:
    """可用倍率里最小且 >= needed 的；都不够就用最大倍率。"""
    scales = _available_scales(model)
    if not scales:
        return int(model.default_scale) or 2
    for scale in scales:
        if scale >= needed:
            return scale
    return scales[-1]


def available_scales(model: ModelSpec | str) -> tuple[int, ...]:
    """模型（或 model_id）在磁盘权重上真实可用的倍率。

    catalog 的 ``scales`` 是声明值；这里按 ``up{N}x-*.param`` 过滤，
    UI 用它渲染倍率下拉框即可避免把不存在的倍率传给 exe。
    """
    spec = get_model(model) if isinstance(model, str) else model
    return _available_scales(spec)


def _resolve_scale(model: ModelSpec, requested) -> tuple[int, str | None]:
    """把请求倍率调整到模型真实支持的倍率，返回 (倍率, 警告)。"""
    try:
        wanted = int(requested)
    except (TypeError, ValueError):
        wanted = int(model.default_scale) or 2
    if wanted < 1:
        fallback = int(model.default_scale) or 2
        return fallback, f"倍率 {requested!r} 无效，已改用 {fallback}×"
    available = _available_scales(model)
    if wanted in available:
        return wanted, None
    effective = _preferred_scale(model, wanted)
    return effective, f"{model.name} 不支持 {wanted}×，已改用 {effective}×"


def scale_passes(model: ModelSpec | str, wanted: int) -> tuple[int, ...]:
    """把请求倍率拆成多轮模型推理（每轮都是模型真实支持的倍率）。

    Real-CUGAN 单轮最多 4×，因此 8×/16×/32× 用组合实现，例如
    32× = 4× → 4× → 2×。请求倍率本身可用时直接单轮返回；
    请求 1× 或更小时返回空元组（不调用模型，只做格式转换）。
    """
    spec = get_model(model) if isinstance(model, str) else model
    try:
        wanted = int(wanted)
    except (TypeError, ValueError):
        wanted = int(spec.default_scale) or 2
    if wanted <= 1:
        return ()
    available = tuple(scale for scale in _available_scales(spec) if scale >= 2)
    if not available:
        return (max(2, int(spec.default_scale) or 2),)
    if wanted in available:
        return (wanted,)

    passes: list[int] = []
    product = 1
    largest = available[-1]
    while product < wanted:
        remaining = wanted / product
        choice = next((scale for scale in reversed(available) if scale <= remaining + 1e-9), None)
        if choice is None:
            choice = largest
        passes.append(choice)
        product *= choice
    return tuple(passes)


def guard_output_size(width: int, height: int, *, warnings: list[str] | None = None) -> None:
    """输出尺寸护栏；超限时抛中文 ValueError（单张失败，不影响整批）。"""
    width = max(1, int(width))
    height = max(1, int(height))
    if width * height > MAX_OUTPUT_PIXELS or max(width, height) > MAX_OUTPUT_SIDE:
        raise ValueError(
            f"输出尺寸过大（约 {width} × {height}，限制为 2.5 亿像素 / 边长 {MAX_OUTPUT_SIDE}），"
            "请降低倍率或改用目标尺寸"
        )


def supported_denoise_levels(model: ModelSpec, scale: int) -> tuple[int, ...]:
    """该模型在该倍率下磁盘上真实存在的降噪等级。

    降噪档位是（模型, 倍率）的函数：例如 models-se 的 2× 有 5 档，
    3×/4× 只有 conservative / no-denoise / denoise3x。这里以权重文件为准，
    按 catalog 的声明顺序返回；目录缺失或该倍率缺权重时退回 catalog 声明。
    """
    declared = tuple(int(level) for level in model.denoise_levels)
    available = _weight_table(model.model_dir).get(int(scale))
    if not available:
        return declared
    matched = tuple(level for level in declared if level in available)
    return matched or tuple(available)


def _resolve_denoise(
    model: ModelSpec,
    scale: int,
    requested: int | None,
) -> tuple[int, str | None]:
    """把降噪等级就近调整到"该倍率下真实存在的权重"，返回 (等级, 警告)。"""
    try:
        wanted = int(model.default_denoise if requested is None else requested)
    except (TypeError, ValueError):
        wanted = int(model.default_denoise)
    candidates = supported_denoise_levels(model, scale)
    if not candidates:
        return wanted, None
    if wanted in candidates:
        return wanted, None
    best = min(candidates, key=lambda level: (abs(level - wanted), level))
    wanted_label = DENOISE_LABELS.get(wanted, str(wanted))
    best_label = DENOISE_LABELS.get(best, str(best))
    return best, f"{model.name} {scale}× 没有「{wanted_label}」权重，已改用「{best_label}」"


# ------------------------------------------------------------- 推理 --
def _notify(progress: Callable[[str, float], None] | None, message: str, fraction: float) -> None:
    if progress is None:
        return
    try:
        progress(message, fraction)
    except Exception:  # noqa: BLE001 - 进度回调不应打断推理
        pass


def _sub_progress(
    progress: Callable[[str, float], None] | None,
    limit: float = 0.88,
) -> Callable[[str, float], None] | None:
    """把 run_ncnn 的 0~1 进度压缩到 upscale_file 的前段。"""
    if progress is None:
        return None

    def report(message: str, fraction: float) -> None:
        _notify(progress, message, max(0.0, min(limit, float(fraction) * limit)))

    return report


def _is_cpu(device: str) -> bool:
    return str(device or "").strip().lower() in ("cpu", "-1")


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or image.mode in ("RGBA", "LA", "PA")


def _model_param_path(model: ModelSpec, scale: int, denoise: int) -> Path:
    if denoise == -1:
        kind = "conservative"
    elif denoise == 0:
        kind = "no-denoise"
    else:
        kind = f"denoise{denoise}x"
    return ncnn_exe().parent / model.model_dir / f"up{scale}x-{kind}.param"


def run_ncnn(
    image: Image.Image,
    scale: int,
    model: ModelSpec,
    denoise: int,
    tile: int,
    tta: bool,
    device: str,
    progress: Callable[[str, float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Image.Image:
    """用临时 PNG 和 exe 交换图像，返回放大后的 PIL 图像（RGB 或 RGBA）。"""
    if cancel and cancel():
        raise UpscaleCancelled("已取消")

    exe = ncnn_exe()
    if not exe.is_file():
        raise RuntimeError(f"未找到 Real-CUGAN 推理程序：{exe}")

    scale = int(scale)
    denoise = int(denoise)
    tile = int(tile)
    if tile < 0:
        tile = 0
    elif 0 < tile < 32:
        tile = 32

    param = _model_param_path(model, scale, denoise)
    if not param.is_file():
        raise RuntimeError(f"缺少模型权重：{param.name}（{model.name} {scale}× 降噪 {denoise}）")

    had_alpha = _has_alpha(image)
    base = image.convert("RGBA" if had_alpha else "RGB")
    alpha = base.getchannel("A") if had_alpha else None
    # ncnn 的 CPU 实现跑 RGBA 会崩（0xC0000005），CPU 模式只送 RGB，alpha 单独放大。
    work = base.convert("RGB") if (had_alpha and _is_cpu(device)) else base

    with tempfile.TemporaryDirectory(prefix="imgforge-cugan-") as tmp:
        work_dir = Path(tmp)
        in_png = work_dir / "input.png"
        out_png = work_dir / "output.png"

        _notify(progress, "准备输入图像", 0.05)
        work.save(in_png, format="PNG")

        cmd = [
            str(exe),
            "-i", str(in_png),
            "-o", str(out_png),
            "-n", str(denoise),
            "-s", str(scale),
            "-m", model.model_dir,
            "-t", str(tile),
            "-f", "png",
        ]
        if tta:
            cmd.append("-x")
        if _is_cpu(device):
            cmd += ["-g", "-1"]
        # 否则不传 -g：让 Vulkan auto 自选物理设备。

        _notify(progress, "调用 Real-CUGAN 推理", 0.2)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            cwd=str(exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancel and cancel():
                    proc.kill()
                    proc.communicate()
                    raise UpscaleCancelled("已取消") from None

        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()
            if not detail:
                detail = (stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"ncnn 推理失败（返回码 {proc.returncode}）：{detail[-600:] or '无输出'}"
            )

        if cancel and cancel():
            raise UpscaleCancelled("已取消")
        _notify(progress, "读取推理结果", 0.9)
        if not out_png.is_file():
            raise RuntimeError("ncnn 未生成输出文件")

        with Image.open(out_png) as out_img:
            out_img.load()
            if out_img.mode not in ("RGB", "RGBA"):
                out_img = out_img.convert("RGBA" if "A" in out_img.getbands() else "RGB")
            result = out_img.copy()

    if had_alpha and alpha is not None and "A" not in result.getbands():
        # exe 丢了透明通道（CPU 路径）：用 LANCZOS 单独放大 alpha 再合并。
        scaled_alpha = alpha.resize(result.size, Image.Resampling.LANCZOS)
        result = result.convert("RGB")
        result.putalpha(scaled_alpha)
    elif had_alpha:
        result = result.convert("RGBA")
    else:
        result = result.convert("RGB")

    _notify(progress, "推理完成", 1.0)
    return result


# --------------------------------------------------------- 输入/输出 --
def _load_image(path: Path) -> tuple[Image.Image, int]:
    """读取第一帧为 RGB/RGBA，返回 (图像, 总帧数)。"""
    try:
        with Image.open(path) as img:
            frames = int(getattr(img, "n_frames", 1))
            if frames > 1:
                img.seek(0)
            img.load()
            img = ImageOps.exif_transpose(img)
            # 调色板 PNG（P 模式）的透明信息在 info 里，convert 成 RGBA 才不会丢 alpha
            has_alpha = _has_alpha(img) or (
                img.mode in ("P", "PA") and "transparency" in img.info
            )
            return img.convert("RGBA" if has_alpha else "RGB"), frames
    except UnidentifiedImageError as exc:
        raise ValueError("无法读取图片：无法识别的图片格式") from exc
    except FileNotFoundError as exc:
        raise ValueError("无法读取图片：文件不存在") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取图片：{exc}") from exc


def _clean_stem(stem: str) -> str:
    cleaned = _ILLEGAL.sub("_", str(stem)).strip().strip(".")
    return cleaned or "image"


def _clean_suffix(suffix: str) -> str:
    return _ILLEGAL.sub("_", str(suffix or "")).strip()


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def _resolve_format(source: Path, requested: str) -> tuple[str, str, list[str]]:
    """返回 (扩展名, 保存格式, 警告列表)。"""
    warnings: list[str] = []
    fmt = str(requested or "keep").strip().lower()
    if fmt in ("", "keep"):
        suffix = source.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".webp"):
            if suffix == ".png":
                return ".png", "png", warnings
            if suffix in (".jpg", ".jpeg"):
                return suffix, "jpg", warnings
            return ".webp", "webp", warnings
        if suffix:
            warnings.append(f"源格式 {suffix} 不适合超分输出，已改用 PNG")
        return ".png", "png", warnings
    if fmt == "jpeg":
        fmt = "jpg"
    entry = _FORMAT_TABLE.get(fmt)
    if entry is None:
        warnings.append(f"不支持的输出格式 {requested!r}，已按源格式处理")
        return _resolve_format(source, "keep")
    return entry[0], entry[1], warnings


def _resolve_destination(
    source: Path,
    options: UpscaleOptions,
    out_root: Path | None,
    *,
    keep_tree: bool,
    base_dir: Path | None,
    ext: str,
) -> Path:
    """算出输出路径；绝不指向源文件本身。"""
    stem = _clean_stem(source.stem) + _clean_suffix(options.suffix)
    if out_root is None:
        dest_dir = source.parent
    elif keep_tree and base_dir is not None:
        try:
            dest_dir = out_root / source.parent.resolve().relative_to(base_dir.resolve())
        except (ValueError, OSError):
            dest_dir = out_root
    else:
        dest_dir = out_root

    candidate = dest_dir / f"{stem}{ext}"
    if _same_path(candidate, source):
        stem = f"{stem}_upscaled"
        candidate = dest_dir / f"{stem}{ext}"

    if options.overwrite:
        return candidate

    index = 2
    while candidate.exists() or _same_path(candidate, source):
        candidate = dest_dir / f"{stem} ({index}){ext}"
        index += 1
    return candidate


def _clamp_quality(quality) -> int:
    try:
        value = int(quality)
    except (TypeError, ValueError):
        value = 92
    return max(1, min(100, value))


def _save_image(image: Image.Image, dest: Path, save_format: str, quality: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if save_format == "png":
        image.save(dest, format="PNG", optimize=True)
        return
    if save_format == "jpg":
        frame = image
        if _has_alpha(frame):
            rgba = frame.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.getchannel("A"))
            frame = canvas
        else:
            frame = frame.convert("RGB")
        frame.save(dest, format="JPEG", quality=_clamp_quality(quality), optimize=True)
        return
    frame = image if _has_alpha(image) else image.convert("RGB")
    frame.save(dest, format="WEBP", quality=_clamp_quality(quality))


def _normalize_device(device, warnings: list[str]) -> str:
    value = str(device or "auto").strip().lower()
    if value in ("auto", "gpu", "cpu"):
        return value
    warnings.append(f"未知设备 {device!r}，已按自动（优先 GPU）处理")
    return "auto"


def _resolve_tile(tile, warnings: list[str]) -> int:
    try:
        value = int(tile)
    except (TypeError, ValueError):
        value = 0
    if value < 0:
        warnings.append(f"分块大小 {value} 无效，已改用自动")
        return 0
    if 0 < value < 32:
        warnings.append(f"分块大小 {value} 小于 32，已改用 32")
        return 32
    return value


def _is_crash_error(exc: RuntimeError) -> bool:
    """ncnn 进程是异常终止（返回码为负，即 Windows 异常码）。"""
    match = _CRASH_RE.search(str(exc))
    return bool(match) and int(match.group(1)) < 0


def _infer(
    image: Image.Image,
    model: ModelSpec,
    scale: int,
    options: UpscaleOptions,
    warnings: list[str],
    progress,
    cancel,
) -> Image.Image:
    if model.backend == "onnx":
        return _infer_onnx(image, model, scale, options, warnings, progress, cancel)

    denoise, note = _resolve_denoise(model, scale, options.denoise)
    if note:
        warnings.append(note)
    tile = _resolve_tile(options.tile, warnings)
    if _is_cpu(options.device) and _has_alpha(image):
        warnings.append("CPU 模式下透明通道单独放大")

    # CPU 路径仍有未覆盖的崩溃可能：崩了自动退回 Vulkan 再试一次。
    attempts = [options.device]
    if _is_cpu(options.device):
        attempts.append("auto")
    last_error: RuntimeError | None = None
    for index, device in enumerate(attempts):
        try:
            return run_ncnn(
                image,
                scale,
                model,
                denoise,
                tile,
                bool(options.tta),
                device,
                progress=_sub_progress(progress),
                cancel=cancel,
            )
        except UpscaleCancelled:
            raise
        except RuntimeError as exc:
            last_error = exc
            if index + 1 < len(attempts) and _is_crash_error(exc):
                warnings.append("CPU 模式不可用，已改用 GPU")
                continue
            raise
    raise last_error  # pragma: no cover - 循环至少会 raise 或 return


def _infer_onnx(
    image: Image.Image,
    model: ModelSpec,
    scale: int,
    options: UpscaleOptions,
    warnings: list[str],
    progress,
    cancel,
) -> Image.Image:
    """APISR（ONNX / DirectML）单轮推理。"""
    from . import onnx_runner  # 延迟导入：避免与 engine 的循环依赖

    if not onnx_runner.models_ready():
        raise RuntimeError(
            "APISR 模型未安装，请在右侧点「下载 APISR 模型」或导入 apisr-onnx-models.zip"
        )
    tile = _resolve_tile(options.tile, warnings)
    return onnx_runner.run_onnx(
        image,
        model.onnx_file,
        scale=scale,
        fixed_input=model.fixed_input,
        device=options.device,
        tile=tile,
        warnings=warnings,
        progress=_sub_progress(progress),
        cancel=cancel,
    )


def _infer_chain(
    image: Image.Image,
    model: ModelSpec,
    passes: tuple[int, ...],
    options: UpscaleOptions,
    warnings: list[str],
    progress,
    cancel,
    *,
    base: float = 0.05,
    span: float = 0.9,
) -> Image.Image:
    """按倍率链连续推理，把进度切成 N 段；每轮都可取消。"""
    if not passes:
        return image
    current = image
    total = len(passes)
    for index, scale in enumerate(passes):
        if cancel and cancel():
            raise UpscaleCancelled("已取消")
        if progress is not None:
            _notify(
                progress,
                f"第 {index + 1}/{total} 轮：模型放大 {scale}×",
                base + span * index / total,
            )
            sub = (
                lambda message, fraction, index=index: _notify(
                    progress,
                    message,
                    base + span * (index + max(0.0, min(1.0, float(fraction)))) / total,
                )
            )
        else:
            sub = None
        current = _infer(current, model, scale, options, warnings, sub, cancel)
    return current


# --------------------------------------------------------- 单文件入口 --
def upscale_file(
    src: Path,
    options: UpscaleOptions,
    out_root: Path | None = None,
    *,
    base_dir: Path | None = None,
    keep_tree: bool = False,
    progress: Callable[[str, float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> UpscaleResult:
    """放大单张图片；除取消外不向外抛异常，失败写进 result.message。"""
    source = Path(src)
    root = Path(out_root) if out_root is not None else None
    base = Path(base_dir) if base_dir is not None else None
    result = UpscaleResult(src=source)
    started = time.perf_counter()

    try:
        if not source.is_file():
            result.message = "文件不存在"
            return result

        _notify(progress, "读取图片", 0.02)
        image, frames = _load_image(source)
        result.src_width, result.src_height = image.size
        if frames > 1:
            result.warnings.append(f"动图只处理第一帧（共 {frames} 帧）")

        options.device = _normalize_device(options.device, result.warnings)
        model = get_model(options.model_id)

        ext, save_format, format_warnings = _resolve_format(source, options.output_format)
        result.warnings.extend(format_warnings)

        if cancel and cancel():
            raise UpscaleCancelled("已取消")

        if options.mode == "target":
            target = compute_target(
                result.src_width,
                result.src_height,
                options.preset,
                options.custom_width,
                options.custom_height,
            )
            if target is None:
                raise ValueError("目标尺寸无效")
            target_w, target_h = target
            width = max(1, result.src_width)
            height = max(1, result.src_height)
            guard_output_size(target_w, target_h)
            needed = max(target_w / width, target_h / height)
            if needed <= 1.0:
                result.warnings.append("源图已达到目标尺寸，仅做缩放")
                _notify(progress, "缩放图像", 0.3)
                image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            else:
                passes = scale_passes(model, int(needed + 0.999))
                if len(passes) > 1:
                    result.warnings.append(
                        f"目标尺寸需要 {len(passes)} 轮模型放大组合（单轮最多 {max(passes)}×），耗时较长"
                    )
                image = _infer_chain(image, model, passes, options, result.warnings, progress, cancel)
                result.model_scale = 1
                for scale in passes:
                    result.model_scale *= scale
                if image.size != (target_w, target_h):
                    _notify(progress, "校正到目标尺寸", 0.9)
                    image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
        else:
            try:
                wanted = max(1, int(options.scale))
            except (TypeError, ValueError):
                wanted = max(1, int(model.default_scale) or 2)
            guard_output_size(result.src_width * wanted, result.src_height * wanted)
            if wanted == 1:
                result.warnings.append("1× 不调用模型，只做格式转换/重新编码")
                image = image
                result.model_scale = 1
            else:
                passes = scale_passes(model, wanted)
                if len(passes) > 1:
                    result.warnings.append(
                        f"{wanted}× 由 {len(passes)} 轮模型放大组合而成（单轮最多 {max(passes)}×），耗时成倍增加"
                    )
                image = _infer_chain(image, model, passes, options, result.warnings, progress, cancel)
                result.model_scale = 1
                for scale in passes:
                    result.model_scale *= scale
                exact = (result.src_width * wanted, result.src_height * wanted)
                if image.size != exact:
                    _notify(progress, "校正到目标倍率", 0.9)
                    image = image.resize(exact, Image.Resampling.LANCZOS)

        dest = _resolve_destination(
            source,
            options,
            root,
            keep_tree=keep_tree,
            base_dir=base,
            ext=ext,
        )
        result.dest = dest
        _notify(progress, "写入输出文件", 0.96)
        _save_image(image, dest, save_format, options.quality)

        result.width, result.height = image.size
        result.ok = True
        result.message = "完成"
    except UpscaleCancelled:
        raise
    except ValueError as exc:
        result.message = str(exc) or "参数无效"
    except PermissionError:
        result.message = "没有写入权限（目标文件可能被占用）"
    except RuntimeError as exc:
        result.message = f"推理失败：{exc}"
    except OSError as exc:
        result.message = f"写入失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 单张坏图不应把整批搞崩
        result.message = f"意外错误：{exc}"
    finally:
        result.seconds = round(time.perf_counter() - started, 3)

    # 多轮推理会重复追加同一类警告（如 CPU 透明通道），去重后展示更干净。
    result.warnings = list(dict.fromkeys(result.warnings))

    if not result.ok and result.dest and result.dest.exists():
        try:
            if result.dest.stat().st_size == 0:
                result.dest.unlink()
        except OSError:
            pass
    return result


# --------------------------------------------------------- 批量入口 --
def run_batch(
    paths: Iterable[str | os.PathLike[str]],
    options: UpscaleOptions,
    out_root: Path | None = None,
    *,
    keep_tree: bool = False,
    base_dir: Path | None = None,
    on_result: Callable[[int, UpscaleResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[UpscaleResult]:
    """逐张放大；should_cancel 为真时提前结束。"""
    items = [Path(path) for path in paths]
    total = len(items)
    root = Path(out_root) if out_root is not None else None
    base = Path(base_dir) if base_dir is not None else None
    results: list[UpscaleResult] = []
    cancelled = False

    for index, path in enumerate(items):
        if should_cancel and should_cancel():
            results.append(UpscaleResult(src=path, message="已取消"))
            cancelled = True
            break
        if on_progress is not None:
            try:
                on_progress(index, total, path.name)
            except Exception:  # noqa: BLE001
                pass
        try:
            result = upscale_file(
                path,
                options,
                root,
                keep_tree=keep_tree,
                base_dir=base,
                cancel=should_cancel,
            )
        except UpscaleCancelled:
            result = UpscaleResult(src=path, message="已取消")
            results.append(result)
            if on_result is not None:
                try:
                    on_result(index, result)
                except Exception:  # noqa: BLE001
                    pass
            cancelled = True
            break
        results.append(result)
        if on_result is not None:
            try:
                on_result(index, result)
            except Exception:  # noqa: BLE001
                pass

    if not cancelled and on_progress is not None:
        try:
            on_progress(total, total, "")
        except Exception:  # noqa: BLE001
            pass
    return results


# --------------------------------------------------------------- 预估 --
def estimate_output_size(width: int, height: int, options: UpscaleOptions) -> tuple[int, int]:
    """按当前选项预估输出尺寸（供 UI 实时显示）。"""
    width = max(1, int(width))
    height = max(1, int(height))
    model = get_model(options.model_id)
    if options.mode == "target":
        target = compute_target(
            width,
            height,
            options.preset,
            options.custom_width,
            options.custom_height,
        )
        return target if target is not None else (width, height)
    try:
        scale = max(1, int(options.scale))
    except (TypeError, ValueError):
        scale = max(1, int(model.default_scale) or 2)
    return (max(1, width * scale), max(1, height * scale))


def available_devices() -> list[tuple[str, str]]:
    """设备下拉框数据：[(id, 显示名), ...]。"""
    return [("auto", "自动（优先 GPU）"), ("gpu", "GPU（Vulkan）"), ("cpu", "CPU")]
