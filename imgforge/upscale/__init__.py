"""图片超分辨率放大：Real-CUGAN ncnn 模型目录 + 目标尺寸计算 + 推理引擎。

对外统一出口，UI 只需要 ``from imgforge import upscale``。

    >>> from imgforge.upscale import UpscaleOptions, upscale_file
    >>> upscale_file(Path("a.png"), UpscaleOptions(model_id="cugan-se", scale=2))
"""

from __future__ import annotations

from .catalog import (
    DEFAULT_MODEL_ID,
    DENOISE_LABELS,
    MODELS,
    MODEL_BY_ID,
    ModelSpec,
    denoise_levels,
    get_model,
    models_sorted,
)
from .engine import (
    UpscaleCancelled,
    UpscaleOptions,
    UpscaleResult,
    available_devices,
    available_scales,
    estimate_output_size,
    ncnn_exe,
    run_batch,
    run_ncnn,
    supported_denoise_levels,
    upscale_file,
    weights_root,
)
from .targets import (
    PRESET_LABELS,
    PRESET_ORDER,
    PRESETS,
    compute_target,
    fit_inside,
    preferred_model_scale,
)

__all__ = [
    # catalog
    "MODELS",
    "MODEL_BY_ID",
    "ModelSpec",
    "models_sorted",
    "get_model",
    "DEFAULT_MODEL_ID",
    "DENOISE_LABELS",
    "denoise_levels",
    "supported_denoise_levels",
    # targets
    "PRESETS",
    "PRESET_ORDER",
    "PRESET_LABELS",
    "fit_inside",
    "compute_target",
    "preferred_model_scale",
    # engine
    "UpscaleOptions",
    "UpscaleResult",
    "UpscaleCancelled",
    "run_ncnn",
    "upscale_file",
    "run_batch",
    "estimate_output_size",
    "available_devices",
    "available_scales",
    "ncnn_exe",
    "weights_root",
]
