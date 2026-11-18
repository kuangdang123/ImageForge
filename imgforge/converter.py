"""转换引擎：格式注册表 + 单文件 / 批量转换。

GUI 与 CLI 都只依赖这里，保证两条入口的行为完全一致。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from . import ico as ico_module

Image.MAX_IMAGE_PIXELS = None  # 大图（海报、扫描件）不触发 DecompressionBomb 警告


class ConvertError(Exception):
    """可展示给用户的转换失败原因。"""


@dataclass(frozen=True)
class FormatSpec:
    key: str
    label: str
    ext: str
    supports_alpha: bool
    supports_animation: bool
    kind: str          # lossy / lossless / icon
    default_quality: int
    note: str


FORMATS: dict[str, FormatSpec] = {
    "png": FormatSpec("png", "PNG", ".png", True, False, "lossless", 100, "无损 · 支持透明"),
    "jpg": FormatSpec("jpg", "JPG", ".jpg", False, False, "lossy", 92, "有损 · 体积小"),
    "webp": FormatSpec("webp", "WebP", ".webp", True, True, "lossy", 88, "高压缩 · 支持动图"),
    "ico": FormatSpec("ico", "ICO", ".ico", True, False, "icon", 100, "图标 · 多尺寸"),
    "bmp": FormatSpec("bmp", "BMP", ".bmp", False, False, "lossless", 100, "无压缩 · 兼容老系统"),
    "tiff": FormatSpec("tiff", "TIFF", ".tiff", True, False, "lossless", 100, "印刷常用"),
    "avif": FormatSpec("avif", "AVIF", ".avif", True, False, "lossy", 62, "新一代压缩 · 较慢"),
}

FORMAT_ORDER = ["ico", "png", "jpg", "webp", "bmp", "tiff", "avif"]

INPUT_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".jpe", ".webp", ".bmp", ".dib",
    ".gif", ".tif", ".tiff", ".ico", ".avif", ".tga", ".ppm", ".pgm", ".pbm",
}

ANIMATED_TARGETS = {"webp"}


@dataclass
class ConvertOptions:
    target: str = "ico"
    quality: int = 92
    lossless: bool = False
    background: tuple[int, int, int] = (255, 255, 255)
    ico_sizes: tuple[int, ...] = ico_module.DEFAULT_SIZES
    ico_fit: str = "contain"
    ico_payload: str = "png"
    ico_upscale: bool = True
    keep_metadata: bool = True
    overwrite: bool = False
    suffix: str = ""

    def spec(self) -> FormatSpec:
        try:
            return FORMATS[self.target]
        except KeyError as exc:
            raise ConvertError(f"不支持的输出格式: {self.target}") from exc


@dataclass
class ImageInfo:
    path: Path
    ok: bool
    format: str = ""
    width: int = 0
    height: int = 0
    frames: int = 1
    mode: str = ""
    bytes: int = 0
    has_alpha: bool = False
    message: str = ""


@dataclass
class ConvertResult:
    src: Path
    dest: Path | None = None
    ok: bool = False
    message: str = ""
    width: int = 0
    height: int = 0
    frames: int = 1
    bytes: int = 0
    renamed: bool = False
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 探测 --

def human_size(num: float) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def probe(path: str | os.PathLike[str]) -> ImageInfo:
    file = Path(path)
    info = ImageInfo(path=file, ok=False)
    try:
        info.bytes = file.stat().st_size
    except OSError as exc:
        info.message = f"读取失败：{exc}"
        return info
    try:
        with Image.open(file) as img:
            info.format = (img.format or file.suffix.lstrip(".")).upper()
            info.width, info.height = img.size
            info.mode = img.mode
            info.frames = int(getattr(img, "n_frames", 1))
            info.has_alpha = _has_alpha(img)
            info.ok = True
    except UnidentifiedImageError:
        info.message = "无法识别的图片格式"
    except Exception as exc:  # noqa: BLE001 - 探测阶段不让坏文件中断整批
        info.message = f"读取失败：{exc}"
    return info


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


# ------------------------------------------------------------------ 载入 --

def _open_image(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
    except UnidentifiedImageError as exc:
        raise ConvertError("无法识别的图片格式（可能损坏或不是图片）") from exc
    except FileNotFoundError as exc:
        raise ConvertError("文件不存在") from exc
    except OSError as exc:
        raise ConvertError(f"打开失败：{exc}") from exc
    try:
        img.load()
    except OSError as exc:
        raise ConvertError("图片数据不完整（可能下载未完成）") from exc
    if img.format in ("JPEG", "MPO", "TIFF") and img.getexif().get(0x0112):
        img = ImageOps.exif_transpose(img)
    return img


def _flatten(img: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def _prepare_static(img: Image.Image, target: str, options: ConvertOptions) -> Image.Image:
    if target in ("jpg", "bmp"):
        return _flatten(img, options.background)
    if _has_alpha(img):
        return img.convert("RGBA")
    return img.convert("RGB")


# ------------------------------------------------------------------ 保存 --

def _save_static(img: Image.Image, dest: Path, options: ConvertOptions) -> None:
    spec = options.spec()
    target = spec.key
    if target == "ico":
        ico_module.save_ico(
            img,
            dest,
            sizes=options.ico_sizes,
            fit=options.ico_fit,
            payload=options.ico_payload,
            upscale=options.ico_upscale,
        )
        return

    frame = _prepare_static(img, target, options)
    params: dict = {}

    icc = img.info.get("icc_profile") if options.keep_metadata else None
    exif = img.info.get("exif") if options.keep_metadata else None

    if target == "png":
        params.update(optimize=True, compress_level=6)
        if icc:
            params["icc_profile"] = icc
    elif target == "jpg":
        params.update(quality=options.quality, optimize=True, progressive=True)
        if icc:
            params["icc_profile"] = icc
        if exif:
            params["exif"] = exif
    elif target == "webp":
        params.update(quality=options.quality, method=4, lossless=options.lossless)
        if icc:
            params["icc_profile"] = icc
        if exif:
            params["exif"] = exif
    elif target == "tiff":
        params.update(compression="tiff_lzw")
        if icc:
            params["icc_profile"] = icc
        if exif:
            params["exif"] = exif
    elif target == "avif":
        params.update(quality=options.quality, speed=6)
        if icc:
            params["icc_profile"] = icc
    elif target == "bmp":
        params.update()
    else:  # pragma: no cover - 注册表与分支一一对应，防御性保底
        raise ConvertError(f"未实现该格式的保存：{target}")

    try:
        frame.save(dest, format=_PIL_FORMAT[target], **params)
    except OSError as exc:
        raise ConvertError(f"写入失败：{exc}") from exc


_PIL_FORMAT = {
    "png": "PNG",
    "jpg": "JPEG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "avif": "AVIF",
}


def _save_animated(img: Image.Image, dest: Path, options: ConvertOptions, info: ImageInfo) -> int:
    target = options.target
    frames = []
    durations = []
    for frame in ImageSequence.Iterator(img):
        duration = frame.info.get("duration", img.info.get("duration", 80)) or 80
        durations.append(max(20, int(duration)))
        frames.append(frame.convert("RGBA" if _has_alpha(frame) else "RGB"))
    loop = int(img.info.get("loop", 0) or 0)
    params: dict = dict(
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=loop,
        optimize=False,
    )
    params.update(quality=options.quality, method=4, lossless=options.lossless)
    try:
        frames[0].save(dest, format=_PIL_FORMAT[target], **params)
    except OSError as exc:
        raise ConvertError(f"写入失败：{exc}") from exc
    return len(frames)


# ------------------------------------------------------------ 输出路径 --

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(name: str) -> str:
    cleaned = _ILLEGAL.sub("_", name).strip().strip(".")
    return cleaned or "image"


def auto_suffix(options: ConvertOptions) -> str:
    """按输出参数生成文件名标签（例如 ``_ico_256px`` / ``_jpg_q92``）。

    用户希望从文件名就能看出「这张图是按什么参数转出来的」，所以默认把
    格式与关键参数写进名字里，而不是只改扩展名。
    """
    spec = options.spec()
    key = spec.key
    if key == "ico":
        sizes = list(options.ico_sizes or [])
        parts = [f"ico_{max(sizes) if sizes else 256}px"]
        if options.ico_fit == "fill":
            parts.append("fill")
        elif options.ico_fit == "stretch":
            parts.append("stretch")
        if options.ico_payload == "bmp":
            parts.append("bmp")
        return "_" + "_".join(parts)
    if key == "webp":
        return "_webp_lossless" if options.lossless else f"_webp_q{options.quality}"
    if spec.kind == "lossy":
        return f"_{key}_q{options.quality}"
    return f"_{key}"

def resolve_destination(
    src: Path,
    options: ConvertOptions,
    out_root: Path | None = None,
    *,
    keep_tree: bool = False,
    base_dir: Path | None = None,
) -> tuple[Path, bool]:
    """算出输出路径；返回 (路径, 是否因重名/同名源文件而改过名)。"""
    spec = options.spec()
    stem = safe_stem(src.stem) + str(options.suffix or "") + auto_suffix(options)
    if out_root is None:
        dest_dir = src.parent
    elif keep_tree and base_dir is not None:
        try:
            dest_dir = out_root / src.parent.resolve().relative_to(base_dir.resolve())
        except ValueError:
            dest_dir = out_root
    else:
        dest_dir = out_root

    candidate = dest_dir / f"{stem}{spec.ext}"
    renamed = False
    # 输出到源文件本身时强制换名，绝不覆盖原图
    if _same_path(candidate, src):
        candidate = dest_dir / f"{stem}_{spec.key}{spec.ext}"
        renamed = True
    if options.overwrite and not _same_path(candidate, src):
        return candidate, renamed

    index = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem} ({index}){spec.ext}"
        renamed = True
        index += 1
    return candidate, renamed


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


# ------------------------------------------------------------ 单文件入口 --

def convert_file(
    src: str | os.PathLike[str],
    options: ConvertOptions,
    out_root: str | os.PathLike[str] | None = None,
    *,
    keep_tree: bool = False,
    base_dir: str | os.PathLike[str] | None = None,
) -> ConvertResult:
    source = Path(src)
    result = ConvertResult(src=source)
    if not source.is_file():
        result.message = "文件不存在"
        return result
    if source.suffix.lower() not in INPUT_EXTS:
        result.message = f"不支持读取 {source.suffix or '该'} 格式"
        return result

    try:
        with _open_image(source) as img:
            frames = int(getattr(img, "n_frames", 1))
            animated = frames > 1 and options.target in ANIMATED_TARGETS
            dest, renamed = resolve_destination(
                source,
                options,
                Path(out_root) if out_root else None,
                keep_tree=keep_tree,
                base_dir=Path(base_dir) if base_dir else None,
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            result.dest = dest
            result.renamed = renamed

            if frames > 1 and not animated:
                result.warnings.append(f"源图有 {frames} 帧，{options.spec().label} 只保留第一帧")

            if animated:
                written = _save_animated(img, dest, options, ImageInfo(source, True))
                result.frames = written
            else:
                _save_static(img, dest, options)
                result.frames = 1

            result.ok = True
            result.message = "完成"
            result.width, result.height = _measure(dest)
            result.bytes = dest.stat().st_size
    except ConvertError as exc:
        result.message = str(exc)
    except PermissionError:
        result.message = "没有写入权限（文件可能被占用）"
    except OSError as exc:
        result.message = f"转换失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 单个坏图不应拖垮整批
        result.message = f"意外错误：{exc}"

    if not result.ok and result.dest and result.dest.exists() and result.dest.stat().st_size == 0:
        try:
            result.dest.unlink()
        except OSError:
            pass
    return result


def _measure(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:  # noqa: BLE001
        return (0, 0)


# ------------------------------------------------------------ 批量入口 --

def run_batch(
    paths: Iterable[str | os.PathLike[str]],
    options: ConvertOptions,
    out_root: str | os.PathLike[str] | None = None,
    *,
    keep_tree: bool = False,
    base_dir: str | os.PathLike[str] | None = None,
    on_result: Callable[[int, ConvertResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[ConvertResult]:
    items = [Path(p) for p in paths]
    results: list[ConvertResult] = []
    for index, path in enumerate(items):
        if should_cancel and should_cancel():
            results.append(ConvertResult(src=path, message="已取消"))
            continue
        result = convert_file(path, options, out_root, keep_tree=keep_tree, base_dir=base_dir)
        results.append(result)
        if on_result:
            on_result(index, result)
    return results


def collect_images(root: str | os.PathLike[str], recursive: bool = True) -> list[Path]:
    folder = Path(root)
    pattern = "**/*" if recursive else "*"
    files = [p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() in INPUT_EXTS]
    return sorted(files, key=lambda p: (str(p).lower()))