"""用户偏好的持久化：%APPDATA%\\ImageForge\\settings.json。

只存能跨会话复用的选择，不存临时状态。文件损坏时静默回退默认值，
不让一个坏 JSON 把应用卡死在启动阶段。开发脚本可用 IMAGEFORGE_HOME
把配置重定向到临时目录，避免污染真实用户配置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def settings_dir() -> Path:
    override = os.environ.get("IMAGEFORGE_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "ImageForge"


DEFAULTS: Dict[str, Any] = {
    "target": "ico",
    "quality_by_format": {key: None for key in ("png", "jpg", "webp", "avif")},
    "lossless": False,
    "ico_sizes": [16, 24, 32, 48, 64, 128, 256],
    "ico_fit": "contain",
    "ico_payload": "png",
    "ico_upscale": True,
    "keep_metadata": True,
    "out_mode": "same",           # same | folder
    "out_dir": "",
    "keep_tree": True,
    "recursive": True,
    "suffix": "",
    "theme": "light",
    "window": "1080x600",
    "last_dir": "",
    "mode": "convert",            # convert | upscale
    # 分辨率放大（Real-CUGAN ncnn）
    "upscale_model": "cugan-pro",
    "upscale_device": "auto",     # auto | gpu | cpu
    "upscale_mode": "scale",      # scale | target
    "upscale_scale": 2,
    "upscale_preset": "1080p",
    "upscale_custom_w": 1920,
    "upscale_custom_h": 1080,
    "upscale_denoise": None,      # None = 用模型默认档
    "upscale_tta": False,
    "upscale_format": "keep",
}


class Settings:
    def __init__(self, data: Dict[str, Any] | None = None, path: Path | None = None) -> None:
        self.path = path or (settings_dir() / "settings.json")
        self.data: Dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        if data:
            self.update(data)

    # ------------------------------------------------------------------ io --
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        target = path or (settings_dir() / "settings.json")
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=target)
        if not isinstance(raw, dict):
            return cls(path=target)
        return cls(raw, path=target)

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.part")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------- access --
    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def update(self, values: Dict[str, Any]) -> None:
        for key, value in values.items():
            if key in DEFAULTS:
                self.data[key] = value

    # ------------------------------------------------------- normalisation --
    def quality(self, target: str, default: int) -> int:
        table = self.data.get("quality_by_format") or {}
        value = table.get(target)
        if isinstance(value, int) and 1 <= value <= 100:
            return value
        return default

    def set_quality(self, target: str, value: int) -> None:
        table = dict(self.data.get("quality_by_format") or {})
        table[target] = int(value)
        self.data["quality_by_format"] = table

    def sizes(self) -> tuple[int, ...]:
        raw = self.data.get("ico_sizes")
        if not isinstance(raw, list):
            return (16, 24, 32, 48, 64, 128, 256)
        clean = sorted({int(v) for v in raw if isinstance(v, int) and 8 <= v <= 256})
        return tuple(clean) or (16, 24, 32, 48, 64, 128, 256)