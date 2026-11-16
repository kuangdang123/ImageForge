"""目标尺寸与倍率选择。

纯计算，不碰文件与推理，方便 UI 实时预估。
"""

from __future__ import annotations

import math

from .catalog import ModelSpec

PRESETS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
}

PRESET_ORDER: list[str] = ["720p", "1080p", "2k", "4k", "8k"]

PRESET_LABELS: dict[str, str] = {
    "720p": "720p",
    "1080p": "1080p",
    "2k": "2K",
    "4k": "4K",
    "8k": "8K",
}


def _round_half_up(value: float) -> int:
    """四舍五入（Python 内置 round 是银行家舍入，不符合直觉）。"""
    return int(math.floor(value + 0.5))


def fit_inside(width: int, height: int, box_w: int, box_h: int) -> tuple[int, int]:
    """按比例缩放到 box 内，保持宽高比；结果至少 1px 且不超出 box。"""
    width, height = int(width), int(height)
    box_w, box_h = int(box_w), int(box_h)
    if width <= 0 or height <= 0 or box_w <= 0 or box_h <= 0:
        return (max(1, width), max(1, height))
    scale = min(box_w / width, box_h / height)
    return (
        max(1, _round_half_up(width * scale)),
        max(1, _round_half_up(height * scale)),
    )


def compute_target(
    width: int,
    height: int,
    preset: str,
    custom_w: int = 0,
    custom_h: int = 0,
) -> tuple[int, int] | None:
    """把用户选的预设换算成"框内等比"的目标尺寸；无效返回 None。"""
    key = str(preset or "").strip().lower()
    if key == "custom":
        try:
            custom_w, custom_h = int(custom_w), int(custom_h)
        except (TypeError, ValueError):
            return None
        if custom_w <= 0 or custom_h <= 0:
            return None
        return fit_inside(width, height, custom_w, custom_h)
    box = PRESETS.get(key)
    if box is None:
        return None
    return fit_inside(width, height, box[0], box[1])


def preferred_model_scale(model: ModelSpec, needed: float) -> int:
    """取模型倍率里最小且 >= needed 的；都不到就用最大倍率。"""
    scales = tuple(sorted(model.scales))
    if not scales:
        return int(model.default_scale) or 2
    needed = float(needed)
    for scale in scales:
        if scale >= needed:
            return scale
    return scales[-1]
