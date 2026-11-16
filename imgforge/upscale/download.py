"""Real-CUGAN ncnn 模型包：下载、校验、解压安装。

模型权重不随 exe 打包（exe 只有 ~45 MB），首次使用放大功能时按需下载；
也支持从本地 zip 安装（离线/内网场景）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .engine import MODELS_DIRNAME, clear_model_cache, default_models_root, models_ready, weights_root

__all__ = [
    "MODELS_URL",
    "MODELS_ARCHIVE",
    "MODELS_SHA256",
    "MODELS_BYTES",
    "DownloadCancelled",
    "DownloadError",
    "download_models",
    "install_archive",
    "models_ready",
]

MODELS_ARCHIVE = "realcugan-ncnn-models.zip"
MODELS_RELEASE_TAG = "models-v1"
MODELS_URL = (
    f"https://github.com/kuangdang123/ImageForge/releases/download/{MODELS_RELEASE_TAG}/{MODELS_ARCHIVE}"
)
#: zip 体积（用于进度条与提示文案）。
MODELS_BYTES = 45_984_346
#: 由 tools/pack_models.py 生成后填入；下载时强校验。
MODELS_SHA256 = "5537212388fd9715cc5091499c1dabe3a9f844bb1bed3639e01e36cd378f8b9c"

ProgressFn = Callable[[int, int], None]
CancelFn = Callable[[], bool]


class DownloadError(RuntimeError):
    """下载或安装模型失败。"""


class DownloadCancelled(DownloadError):
    """用户取消了下载。"""


def _require_sha() -> str:
    if not MODELS_SHA256:
        raise DownloadError("模型包校验值缺失，请重新打包并更新 MODELS_SHA256")
    return MODELS_SHA256.lower()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_archive(archive: Path, root: Path | None = None) -> Path:
    """把模型 zip 解压到模型根目录（zip 内应包含 ncnn/ 目录）。"""
    archive = Path(archive)
    if not archive.is_file():
        raise DownloadError(f"找不到模型包：{archive}")
    target = Path(root) if root else weights_root()
    target.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix="imgforge-models-"))
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                parts = [p for p in name.split("/") if p not in ("", ".")]
                if not parts or any(p == ".." for p in parts) or ":" in parts[0]:
                    raise DownloadError(f"模型包内含非法路径：{info.filename}")
                if parts[0] != "ncnn":
                    continue
                zf.extract(info, staging)
        extracted = staging / "ncnn"
        if not extracted.is_dir():
            raise DownloadError("模型包结构不正确（缺少 ncnn 目录）")
        if not any(extracted.glob("models-*/up*x-*.param")):
            raise DownloadError("模型包不完整（没有找到模型权重）")

        final = target / "ncnn"
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        shutil.move(str(extracted), str(final))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    clear_model_cache()
    return target


def download_models(
    root: Path | None = None,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    timeout: float = 60.0,
    url: str | None = None,
) -> Path:
    """下载并安装模型包；返回模型根目录。已在安装目录时用 ``install_archive``。"""
    target = Path(root) if root else default_models_root()
    target.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix="imgforge-models-", suffix=".zip.download")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        request = urllib.request.Request(url or MODELS_URL, headers={"User-Agent": "ImageForge"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if cancel and cancel():
                            raise DownloadCancelled("已取消模型下载")
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except urllib.error.HTTPError as exc:
            raise DownloadError(f"下载失败（HTTP {exc.code}），请稍后重试或手动下载后从文件安装") from exc
        except urllib.error.URLError as exc:
            raise DownloadError(f"网络不可用：{exc.reason}") from exc

        digest = _sha256_of(tmp)
        if digest != _require_sha():
            raise DownloadError("模型包校验失败，文件可能不完整，请重试")
        return install_archive(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
