"""APISR 超分模型的 ONNX Runtime 推理（DirectML GPU / CPU）。

模型来自 APISR（CVPR 2024），导出为 ONNX 后由 onnxruntime 推理。本模块只
负责「PIL Image 进、PIL Image 出」：选 provider、建/缓存会话、按瓦片推理、
重叠处线性融合、透明通道用 LANCZOS 单独处理、错误翻译成中文。引擎
``engine.py`` 在调用处延迟 import 本模块，所以这里可以顶层 import engine
里的公共设施，不会形成循环依赖。

实测注意点（勿改回）：
* 三个模型都是 4×：``grl4`` / ``dat4`` 的输入固定为 128×128（窗口注意力），
  ``rrdb4`` 是动态 H/W；固定输入模型必须切 128×128 瓦片（步长 96），否则
  尺寸对不上；
* 模型输出会略微越界（实测约 -0.33 ~ 1.27），必须 clip 到 0..1 再转 uint8；
* DML 首次建会话要编译图（本机 1.6~6.2s），会话缓存后 128×128 单块约
  0.45s（grl）/ 0.6s（rrdb）/ 2.9s（dat），CPU 慢约 5~9 倍；
* onnxruntime-directml 的 DML EP 初始化失败时要静默退回 CPU，并且给用户
  中文提示，不能让英文堆栈直接冒出去；
* 瓦片边缘用 numpy 边缘复制补到固定尺寸，重叠处用互补线性权重融合，避免
  接缝；边缘补出来的像素只参与计算、不写回结果。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .engine import UpscaleCancelled, _has_alpha, _notify, weights_root

__all__ = [
    "ONNX_DIRNAME",
    "TILE_OVERLAP",
    "DEFAULT_TILE",
    "runtime_available",
    "available_providers",
    "describe_backend",
    "providers_for",
    "models_dir",
    "model_path",
    "models_ready",
    "run_onnx",
]

#: ONNX 模型所在的子目录名：``weights_root()/apisr/``。
ONNX_DIRNAME = "apisr"

#: 固定尺寸模型相邻瓦片的重叠像素（融合带宽度）。
TILE_OVERLAP = 32

#: 固定尺寸模型的瓦片边长（必须等于模型的固定输入）。
DEFAULT_TILE = 128

#: 动态模型整图推理的尺寸上限（超过则切瓦片，避免显存/内存爆掉）。
_WHOLE_IMAGE_MAX = 512

#: 动态模型在 tile=0 且整图太大时使用的瓦片边长。
_DEFAULT_DYNAMIC_TILE = 256

#: 动态模型允许的最小瓦片边长（再小融合带就超过瓦片本身了）。
_MIN_TILE = 64


# --------------------------------------------------- onnxruntime 运行时 --
_ORT: Any = None
_ORT_FAILED = False


def _ort() -> Any:
    """延迟 import onnxruntime；不可用时返回 None（并记住失败，不反复重试）。"""
    global _ORT, _ORT_FAILED
    if _ORT is not None:
        return _ORT
    if _ORT_FAILED:
        return None
    try:
        import onnxruntime as ort  # noqa: F401 - 只需要模块对象
    except Exception:  # noqa: BLE001 - 原生 DLL 缺失等异常类型不稳定
        _ORT_FAILED = True
        return None
    _ORT = ort
    return _ORT


def runtime_available() -> bool:
    """onnxruntime 是否可用（装没装、DLL 能不能加载）。"""
    return _ort() is not None


def available_providers() -> list[str]:
    """onnxruntime 实际注册的 ExecutionProvider 名称；运行时不可用时为空。"""
    ort = _ort()
    if ort is None:
        return []
    try:
        return [str(name) for name in ort.get_available_providers()]
    except Exception:  # noqa: BLE001 - 查询失败按不可用处理
        return []


def describe_backend() -> str:
    """给 UI 的短描述：DirectML（GPU） / CPU。"""
    if not runtime_available():
        return "ONNX 运行时不可用"
    if "DmlExecutionProvider" in available_providers():
        return "DirectML（GPU）"
    return "CPU"


def _device_key(device: str) -> str:
    value = str(device or "auto").strip().lower()
    if value in ("cpu", "-1"):
        return "cpu"
    if value in ("gpu", "dml", "directml", "cuda"):
        return "gpu"
    return "auto"


def providers_for(device: str) -> list[str]:
    """按设备偏好给出 provider 列表。

    * ``auto``：有 DirectML 就用 DML（后面跟 CPU 兜底），否则纯 CPU；
    * ``gpu``：同样优先 DML，但没装 DML 时也只返回 CPU（由调用方提示用户）；
    * ``cpu``：只用 CPU。
    """
    if _device_key(device) == "cpu":
        return ["CPUExecutionProvider"]
    if "DmlExecutionProvider" in available_providers():
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


# --------------------------------------------------------------- 路径 --
def models_dir() -> Path:
    """ONNX 模型目录：``weights_root()/apisr/``。"""
    return weights_root() / ONNX_DIRNAME


def model_path(filename: str) -> Path:
    """某个 ONNX 模型文件的完整路径。"""
    return models_dir() / filename


def models_ready() -> bool:
    """模型目录里是否至少有一个 .onnx 文件（够不够用由文件名决定）。"""
    try:
        entries = list(models_dir().iterdir())
    except OSError:
        return False
    return any(entry.is_file() and entry.suffix.lower() == ".onnx" for entry in entries)


# ----------------------------------------------------------- 瓦片规划 --
def _plan_tiles(
    width: int,
    height: int,
    tile: int,
    overlap: int,
) -> list[tuple[int, int, int, int]]:
    """把 ``width×height`` 切成瓦片，返回行优先的 ``(x0, y0, x1, y1)`` 列表。

    坐标是**原图像素**且不越界：最后一列/行不足一个瓦片时只返回剩余部分，
    固定输入模型由调用方把这一块用边缘复制补到 ``tile×tile``。相邻瓦片在
    有空间时保证重叠 ``overlap`` 像素（最后一列被裁短时重叠仍然成立）。
    融合带最多取瓦片边长的一半，避免步长退化成 1 导致瓦片数量爆炸。
    """
    width, height, tile = int(width), int(height), int(tile)
    if width <= 0 or height <= 0:
        raise ValueError("图像尺寸必须为正数")
    if tile <= 0:
        raise ValueError("瓦片大小必须为正数")
    overlap = max(0, min(int(overlap), tile // 2))
    step = max(1, tile - overlap)
    columns = _axis_spans(width, tile, step)
    rows = _axis_spans(height, tile, step)
    return [(x0, y0, x1, y1) for y0, y1 in rows for x0, x1 in columns]


def _axis_spans(length: int, tile: int, step: int) -> list[tuple[int, int]]:
    """单轴切分：[0, tile), [step, step+tile), … 直到覆盖 ``length``。"""
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + tile, length)
        spans.append((start, end))
        if end >= length:
            return spans
        start += step


def _blend_weight(size: int, overlap: int) -> np.ndarray:
    """单轴线性渐变权重：边缘小、中间 1，重叠处两块互补相加正好为 1。

    权重恒大于 0（最小 ``1/(overlap+1)``），所以按权重和归一化时不会出现
    除零；即使某个边缘像素只有一块瓦片覆盖，归一化后也能还原原值。
    """
    size = int(size)
    if size <= 0:
        raise ValueError("权重尺寸必须为正数")
    overlap = max(0, min(int(overlap), size - 1))
    if overlap == 0:
        return np.ones(size, dtype=np.float32)
    index = np.arange(size, dtype=np.float32)
    rising = (index + 1.0) / (overlap + 1.0)
    falling = (size - index) / (overlap + 1.0)
    return np.minimum(np.minimum(rising, falling), 1.0).astype(np.float32, copy=False)


# --------------------------------------------------------- 会话缓存 --
@dataclass(frozen=True)
class _SessionEntry:
    """一次会话加载的产物：会话本身 + 输入输出名 + 静态输入尺寸。"""

    session: Any
    input_name: str
    output_name: str
    static_hw: tuple[int, int] | None = None


#: key = (模型绝对路径, providers 元组, mtime_ns)；DML 首次建会话很贵，只建一次。
_SESSION_CACHE: dict[tuple[str, tuple[str, ...], int], _SessionEntry] = {}
_SESSION_LOCK = threading.Lock()


def _load_session(
    path: Path,
    providers: list[str],
    warnings: list[str] | None,
) -> _SessionEntry:
    """取缓存会话；没有则创建（同一把锁避免并发重复加载）。"""
    try:
        stamp = int(path.stat().st_mtime_ns)
    except OSError as exc:
        raise RuntimeError(f"缺少模型文件：{path.name}") from exc
    absolute = str(path.resolve())
    key = (absolute, tuple(providers), stamp)
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(key)
        if cached is not None:
            return cached
        entry = _create_session(path, providers, warnings)
        # 同一模型换了 providers / 文件被重新导出时丢掉旧会话，避免缓存堆积。
        for old_key in [item for item in _SESSION_CACHE if item[0] == absolute and item != key]:
            _SESSION_CACHE.pop(old_key, None)
        _SESSION_CACHE[key] = entry
        return entry


def _create_session(
    path: Path,
    providers: list[str],
    warnings: list[str] | None,
) -> _SessionEntry:
    ort = _ort()
    if ort is None:
        raise RuntimeError("ONNX 运行时不可用（请重新安装程序）")

    plans: list[tuple[str, ...]] = [tuple(providers)]
    if "DmlExecutionProvider" in providers:
        # DML 初始化可能在原生库内部失败（驱动/显卡不支持），退回纯 CPU 再试。
        plans.append(("CPUExecutionProvider",))
    for index, plan in enumerate(plans):
        try:
            session = ort.InferenceSession(str(path), providers=list(plan))
        except Exception as exc:  # noqa: BLE001 - onnxruntime 异常类型不稳定
            if index + 1 < len(plans):
                if warnings is not None:
                    warnings.append("DirectML 初始化失败，已改用 CPU")
                continue
            raise RuntimeError(f"无法加载模型 {path.name}：{exc}") from exc
        actual = [str(name) for name in session.get_providers()]
        if "DmlExecutionProvider" in plan and "DmlExecutionProvider" not in actual:
            if warnings is not None:
                warnings.append("GPU 推理不可用，已改用 CPU")
        return _make_entry(session, path)
    raise RuntimeError(f"无法加载模型 {path.name}")  # pragma: no cover - 循环必然 return/raise


def _make_entry(session: Any, path: Path) -> _SessionEntry:
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if not inputs or not outputs:
        raise RuntimeError(f"模型 {path.name} 缺少输入/输出节点")
    static_hw: tuple[int, int] | None = None
    shape = getattr(inputs[0], "shape", None) or []
    if len(shape) == 4:
        height, width = shape[2], shape[3]
        if isinstance(height, int) and isinstance(width, int) and height > 0 and width > 0:
            static_hw = (int(height), int(width))
    return _SessionEntry(
        session=session,
        input_name=str(inputs[0].name),
        output_name=str(outputs[0].name),
        static_hw=static_hw,
    )


# --------------------------------------------------------- 推理工具 --
def _is_cancelled(cancel: Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    try:
        return bool(cancel())
    except Exception:  # noqa: BLE001 - 取消回调不应打断推理
        return False


def _infer_patch(entry: _SessionEntry, tile: np.ndarray) -> np.ndarray:
    """跑一块图：uint8 HWC RGB → float32 HWC（0..255，已 clip 到模型合法值域）。"""
    blob = tile.astype(np.float32) / 255.0
    blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None, ...])
    try:
        outputs = entry.session.run([entry.output_name], {entry.input_name: blob})
    except Exception as exc:  # noqa: BLE001 - onnxruntime 异常类型不稳定
        raise RuntimeError(f"ONNX 推理失败：{exc}") from exc
    if not outputs:
        raise RuntimeError("ONNX 推理没有返回结果")
    result = np.asarray(outputs[0], dtype=np.float32)
    if result.ndim != 4 or result.shape[0] < 1 or result.shape[1] < 3:
        raise RuntimeError(f"模型输出形状异常：{tuple(result.shape)}")
    patch = result[0, :3].transpose(1, 2, 0)
    # 输出会越界到 0..1 之外，先 clip 再换算成 0..255 的浮点图。
    return np.clip(patch, 0.0, 1.0) * 255.0


def _output_factor(in_h: int, in_w: int, out_h: int, out_w: int) -> int:
    """由输入/输出尺寸反推放大倍率；不合法就抛中文错误。"""
    if in_h <= 0 or in_w <= 0:
        raise RuntimeError("模型输入尺寸异常")
    if out_h < in_h or out_w < in_w or out_h % in_h or out_w % in_w:
        raise RuntimeError(f"模型输出尺寸异常：{out_w}×{out_h}（输入 {in_w}×{in_h}）")
    factor_h = out_h // in_h
    factor_w = out_w // in_w
    if factor_h != factor_w:
        raise RuntimeError(f"模型横向/纵向放大倍率不一致：{factor_w}× / {factor_h}×")
    return factor_h


def _note_scale(scale: int, factor: int, warnings: list[str] | None) -> None:
    if warnings is not None and scale > 0 and factor > 0 and scale != factor:
        warnings.append(f"模型实际放大 {factor}×，已按 {factor}× 输出")


def _accumulate(
    acc: np.ndarray,
    weight_acc: np.ndarray,
    patch: np.ndarray,
    rect: tuple[int, int, int, int],
    factor: int,
    overlap: int,
) -> None:
    """把一块输出按渐变权重叠进整图缓冲区（输出坐标 = 原图坐标 × factor）。"""
    x0, y0, x1, y1 = rect
    width = x1 - x0
    height = y1 - y0
    valid = patch[: height * factor, : width * factor]
    if valid.shape[0] != height * factor or valid.shape[1] != width * factor:
        raise RuntimeError(
            f"模型输出尺寸异常：期望 {width * factor}×{height * factor}，"
            f"实际 {valid.shape[1]}×{valid.shape[0]}"
        )
    weights = np.outer(
        np.repeat(_blend_weight(height, overlap), factor),
        np.repeat(_blend_weight(width, overlap), factor),
    )
    left = x0 * factor
    top = y0 * factor
    right = left + width * factor
    bottom = top + height * factor
    acc[top:bottom, left:right] += valid * weights[..., None]
    weight_acc[top:bottom, left:right] += weights


def _merge(acc: np.ndarray, weight_acc: np.ndarray, factor: int, width: int, height: int) -> np.ndarray:
    """按权重和归一化 → uint8 RGB，并裁到精确的 ``width*scale × height*scale``。"""
    merged = acc / np.maximum(weight_acc, 1e-6)[..., None]
    np.clip(merged, 0.0, 255.0, out=merged)
    result = np.rint(merged).astype(np.uint8)
    return result[: height * factor, : width * factor]


def _check_no_tiles(acc: np.ndarray | None, factor: int) -> None:
    if acc is None or factor <= 0:
        raise RuntimeError("推理未产生任何瓦片")


# ------------------------------------------------------- 固定尺寸模型 --
def _run_fixed(
    rgb: np.ndarray,
    entry: _SessionEntry,
    fixed_size: int,
    scale: int,
    warnings: list[str] | None,
    progress: Callable[[str, float], None] | None,
    cancel: Callable[[], bool] | None,
) -> np.ndarray:
    """固定 128×128（或模型实际固定边长）输入：切瓦片 + 边缘补边 + 渐变融合。"""
    height, width = rgb.shape[:2]
    rects = _plan_tiles(width, height, fixed_size, TILE_OVERLAP)
    total = len(rects)
    acc: np.ndarray | None = None
    weight_acc: np.ndarray | None = None
    factor = 0
    for index, rect in enumerate(rects, 1):
        if _is_cancelled(cancel):
            raise UpscaleCancelled("已取消")
        _notify(progress, f"第 {index}/{total} 块", index / total)
        x0, y0, x1, y1 = rect
        tile = rgb[y0:y1, x0:x1]
        pad_h = fixed_size - tile.shape[0]
        pad_w = fixed_size - tile.shape[1]
        if pad_h > 0 or pad_w > 0:
            # 边缘复制补到模型固定输入；补出来的像素不写回结果（下面只取有效区）。
            tile = np.pad(tile, ((0, max(0, pad_h)), (0, max(0, pad_w)), (0, 0)), mode="edge")
        patch = _infer_patch(entry, tile)
        out_factor = _output_factor(fixed_size, fixed_size, patch.shape[0], patch.shape[1])
        if factor == 0:
            factor = out_factor
            _note_scale(scale, factor, warnings)
            acc = np.zeros((height * factor, width * factor, 3), dtype=np.float32)
            weight_acc = np.zeros((height * factor, width * factor), dtype=np.float32)
        elif out_factor != factor:
            raise RuntimeError(f"模型输出倍率不稳定：{factor}× / {out_factor}×")
        assert acc is not None and weight_acc is not None
        _accumulate(acc, weight_acc, patch, rect, factor, TILE_OVERLAP)
    _check_no_tiles(acc, factor)
    if _is_cancelled(cancel):
        raise UpscaleCancelled("已取消")
    assert acc is not None and weight_acc is not None
    return _merge(acc, weight_acc, factor, width, height)


# --------------------------------------------------------- 动态尺寸模型 --
def _run_dynamic(
    rgb: np.ndarray,
    entry: _SessionEntry,
    tile: int,
    scale: int,
    warnings: list[str] | None,
    progress: Callable[[str, float], None] | None,
    cancel: Callable[[], bool] | None,
) -> np.ndarray:
    """动态 H/W 模型：小图整图推理，大图或用户指定 tile 时切瓦片融合。"""
    height, width = rgb.shape[:2]
    requested = int(tile or 0)
    if requested > 0:
        if requested < _MIN_TILE:
            if warnings is not None:
                warnings.append(f"瓦片大小 {requested} 小于 {_MIN_TILE}，已改用 {_MIN_TILE}")
            requested = _MIN_TILE
        rects = _plan_tiles(width, height, requested, TILE_OVERLAP)
        overlap = TILE_OVERLAP
    elif max(width, height) <= _WHOLE_IMAGE_MAX:
        rects = [(0, 0, width, height)]
        overlap = 0
    else:
        rects = _plan_tiles(width, height, _DEFAULT_DYNAMIC_TILE, TILE_OVERLAP)
        overlap = TILE_OVERLAP
    tiled = len(rects) > 1
    total = len(rects)
    acc: np.ndarray | None = None
    weight_acc: np.ndarray | None = None
    factor = 0
    for index, rect in enumerate(rects, 1):
        if _is_cancelled(cancel):
            raise UpscaleCancelled("已取消")
        if tiled:
            _notify(progress, f"第 {index}/{total} 块", index / total)
        else:
            _notify(progress, "推理整图", 0.1)
        x0, y0, x1, y1 = rect
        patch = _infer_patch(entry, np.ascontiguousarray(rgb[y0:y1, x0:x1]))
        out_factor = _output_factor(y1 - y0, x1 - x0, patch.shape[0], patch.shape[1])
        if factor == 0:
            factor = out_factor
            _note_scale(scale, factor, warnings)
            acc = np.zeros((height * factor, width * factor, 3), dtype=np.float32)
            weight_acc = np.zeros((height * factor, width * factor), dtype=np.float32)
        elif out_factor != factor:
            raise RuntimeError(f"模型输出倍率不稳定：{factor}× / {out_factor}×")
        assert acc is not None and weight_acc is not None
        _accumulate(acc, weight_acc, patch, rect, factor, overlap)
    _check_no_tiles(acc, factor)
    if _is_cancelled(cancel):
        raise UpscaleCancelled("已取消")
    assert acc is not None and weight_acc is not None
    return _merge(acc, weight_acc, factor, width, height)


# ----------------------------------------------------------- 公开入口 --
def run_onnx(
    image: Image.Image,
    filename: str,
    *,
    scale: int = 4,
    fixed_input: int = 0,
    device: str = "auto",
    tile: int = 0,
    warnings: list[str] | None = None,
    progress: Callable[[str, float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Image.Image:
    """用 ONNX 超分模型放大 ``image``，返回新的 PIL Image。

    Args:
        image: 任意 Pillow 图（RGBA/L/P 都行）；有 alpha 时 RGB 走模型、
            alpha 用 LANCZOS 单独放大后合并。
        filename: 模型文件名，例如 ``"grl4.onnx"``。
        scale: 期望放大倍率（模型都是 4×；实际以模型输出为准）。
        fixed_input: 0=动态尺寸模型；128=固定 128×128 输入（窗口注意力模型）。
        device: ``"auto"`` / ``"gpu"`` / ``"cpu"``，gpu 不可用时自动退回 CPU。
        tile: 仅动态模型有效；0=自动，>=64 时强制该瓦片大小。
        warnings: 可选列表，用于追加中文提示（透明通道、GPU 回退等）。
        progress: ``progress(消息, 0..1)`` 进度回调，异常会被吞掉。
        cancel: ``cancel()`` 返回 True 时尽快抛 ``UpscaleCancelled``。

    Raises:
        RuntimeError: 运行时/模型缺失、模型加载或推理失败（中文提示）。
        UpscaleCancelled: 用户取消。
    """
    if not isinstance(image, Image.Image):
        raise TypeError("需要 Pillow Image 对象")
    if image.width <= 0 or image.height <= 0:
        raise ValueError("图像尺寸无效")
    if not runtime_available():
        raise RuntimeError("ONNX 运行时不可用（请重新安装程序）")

    path = model_path(filename)
    if not path.is_file():
        raise RuntimeError(f"缺少模型文件：{path.name}")
    if _is_cancelled(cancel):
        raise UpscaleCancelled("已取消")

    key = _device_key(device)
    providers = providers_for(key)
    if key == "gpu" and "DmlExecutionProvider" not in providers:
        if warnings is not None:
            warnings.append("GPU 推理不可用，已改用 CPU")

    had_alpha = _has_alpha(image)
    alpha: Image.Image | None = None
    if had_alpha:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        rgb_image = rgba.convert("RGB")
        if warnings is not None:
            warnings.append("透明通道用 LANCZOS 放大")
    else:
        rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image, dtype=np.uint8)

    _notify(progress, "加载模型", 0.02)
    entry = _load_session(path, providers, warnings)
    if _is_cancelled(cancel):
        raise UpscaleCancelled("已取消")

    if entry.static_hw is not None and entry.static_hw[0] != entry.static_hw[1]:
        raise RuntimeError(
            f"模型固定输入 {entry.static_hw[1]}×{entry.static_hw[0]} 不是正方形，暂不支持"
        )

    requested_scale = int(scale) if scale else 0
    fixed = int(fixed_input or 0)
    if fixed > 0:
        fixed_size = fixed
        if entry.static_hw is not None and int(entry.static_hw[0]) != fixed_size:
            fixed_size = int(entry.static_hw[0])
            if warnings is not None:
                warnings.append(f"模型固定输入为 {fixed_size}×{fixed_size}，已按模型输入推理")
        out = _run_fixed(rgb, entry, fixed_size, requested_scale, warnings, progress, cancel)
    elif entry.static_hw is not None:
        fixed_size = int(entry.static_hw[0])
        if warnings is not None:
            warnings.append(f"模型为固定 {fixed_size}×{fixed_size} 输入，已自动按瓦片推理")
        out = _run_fixed(rgb, entry, fixed_size, requested_scale, warnings, progress, cancel)
    else:
        out = _run_dynamic(rgb, entry, int(tile or 0), requested_scale, warnings, progress, cancel)

    result = Image.fromarray(out, mode="RGB")
    if alpha is not None:
        alpha_out = alpha.resize(result.size, Image.Resampling.LANCZOS)
        result = Image.merge("RGBA", (*result.split(), alpha_out))
    _notify(progress, "推理完成", 1.0)
    return result
