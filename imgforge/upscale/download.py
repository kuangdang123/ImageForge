"""模型包管理：按需下载、校验、解压安装。

模型权重不随 exe / 源码打包，首次使用时从 GitHub release 资产下载；也支持
从本地 zip 安装（离线 / 内网场景）。目前有两个包（见 :data:`PACKS`）：

* ``ncnn``  —— Real-CUGAN 推理程序与权重（格式转换、超分的基础，必需）；
* ``apisr`` —— APISR 动漫 4× 超分 ONNX 模型（可选）。

两个包都安装在「模型根目录」下的同名子目录里（``<root>/ncnn``、
``<root>/apisr``），zip 内的顶层目录名与子目录名一致，因此同一个 zip
既能用于自动下载，也能让用户手动选择文件安装。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .engine import clear_model_cache, default_models_root, models_ready, weights_root

__all__ = [
    # 元数据
    "ModelPack",
    "PACKS",
    "PACK_BY_KEY",
    # 历史常量（ncnn）与新常量（apisr）
    "MODELS_URL",
    "MODELS_ARCHIVE",
    "MODELS_RELEASE_TAG",
    "MODELS_SHA256",
    "MODELS_BYTES",
    "APISR_TAG",
    "APISR_ARCHIVE",
    "APISR_SHA256",
    "APISR_BYTES",
    # 异常
    "DownloadCancelled",
    "DownloadError",
    # 新 API
    "models_url",
    "pack_url",
    "pack_root",
    "installed",
    "installed_packs",
    "missing_packs",
    "install_archive",
    "download_pack",
    # 旧 API
    "download_models",
    "models_ready",
]

#: GitHub 仓库（release 资产下载地址）。
REPO = "kuangdang123/ImageForge"
RELEASES_URL = f"https://github.com/{REPO}/releases/download"


@dataclass(frozen=True)
class ModelPack:
    """一个可下载的模型包。"""

    key: str        # "ncnn" / "apisr"
    label: str      # 中文短名，如 "Real-CUGAN 模型" / "APISR 模型"
    archive: str    # release 资产文件名
    tag: str        # release tag（如 models-v1 / models-v2）
    size: int       # zip 字节数
    sha256: str     # zip 的 sha256（小写十六进制）
    subdir: str     # zip 内顶层目录，也是安装到模型根目录下的子目录名
    required: bool  # ncnn=True（格式转换/超分基础），apisr=False
    note: str       # 一句话说明（中文）


# --------------------------------------------------------------- 常量 --
MODELS_ARCHIVE = "realcugan-ncnn-models.zip"
MODELS_RELEASE_TAG = "models-v1"
#: zip 体积（用于进度条与提示文案）。
MODELS_BYTES = 45_984_346
#: 由 tools/pack_models.py 生成后填入；下载时强校验。
MODELS_SHA256 = "5537212388fd9715cc5091499c1dabe3a9f844bb1bed3639e01e36cd378f8b9c"

APISR_ARCHIVE = "apisr-onnx-models.zip"
APISR_TAG = "models-v2"
APISR_BYTES = 61_543_814
APISR_SHA256 = "2608eeccc8f76a0fdde2999612a4fdc4a140226a3e4105a44ef6e5b809468aa7"


PACKS: tuple[ModelPack, ...] = (
    ModelPack(
        key="ncnn",
        label="Real-CUGAN 模型",
        archive=MODELS_ARCHIVE,
        tag=MODELS_RELEASE_TAG,
        size=MODELS_BYTES,
        sha256=MODELS_SHA256,
        subdir="ncnn",
        required=True,
        note="Real-CUGAN 超分与格式转换的基础模型（ncnn / Vulkan）",
    ),
    ModelPack(
        key="apisr",
        label="APISR 模型",
        archive=APISR_ARCHIVE,
        tag=APISR_TAG,
        size=APISR_BYTES,
        sha256=APISR_SHA256,
        subdir="apisr",
        required=False,
        note="APISR 动漫 4× 超分 ONNX 模型（可选）",
    ),
)

PACK_BY_KEY: dict[str, ModelPack] = {pack.key: pack for pack in PACKS}

MODELS_URL = f"{RELEASES_URL}/{MODELS_RELEASE_TAG}/{MODELS_ARCHIVE}"
APISR_URL = f"{RELEASES_URL}/{APISR_TAG}/{APISR_ARCHIVE}"

ProgressFn = Callable[[int, int], None]
CancelFn = Callable[[], bool]


class DownloadError(RuntimeError):
    """下载或安装模型失败。"""


class DownloadCancelled(DownloadError):
    """用户取消了下载。"""


# --------------------------------------------------------------- 元数据 --
def models_url(tag: str, archive: str) -> str:
    """GitHub release 资产下载地址。"""
    return f"{RELEASES_URL}/{tag}/{archive}"


def pack_url(pack: ModelPack) -> str:
    """模型包的下载地址。"""
    return models_url(pack.tag, pack.archive)


def _resolve_pack(pack_or_key: ModelPack | str) -> ModelPack:
    if isinstance(pack_or_key, ModelPack):
        return pack_or_key
    key = str(pack_or_key)
    try:
        return PACK_BY_KEY[key]
    except KeyError:
        raise DownloadError(f"未知模型包：{key}") from None


def _base_root(root: Path | str | None) -> Path:
    """模型根目录：显式给定的，否则用当前生效的模型根目录。"""
    return Path(root) if root else weights_root()


def pack_root(pack: ModelPack | str, root: Path | str | None = None) -> Path:
    """模型包在模型根目录下对应的文件夹。"""
    return _base_root(root) / _resolve_pack(pack).subdir


# ------------------------------------------------------------- 安装状态 --
def _has_onnx(folder: Path) -> bool:
    try:
        return any(
            child.is_file() and child.suffix.lower() == ".onnx" for child in folder.iterdir()
        )
    except OSError:
        return False


def _dir_has_entries(folder: Path) -> bool:
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return False


def _pack_installed(pack: ModelPack, base: Path) -> bool:
    folder = base / pack.subdir
    if not folder.is_dir():
        return False
    if pack.key == "ncnn":
        # ncnn 包的核心是推理程序；权重由 engine.models_ready() 再检查。
        return (folder / "realcugan-ncnn-vulkan.exe").is_file()
    if pack.key == "apisr":
        return _has_onnx(folder)
    return _dir_has_entries(folder)


def installed(pack: ModelPack | str, root: Path | str | None = None) -> bool:
    """模型包是否已安装。

    * ``ncnn``：``<subdir>/realcugan-ncnn-vulkan.exe`` 存在；
    * ``apisr``：``<subdir>/`` 下至少一个 ``*.onnx``；
    * 未知 key：目录存在且非空。
    """
    base = _base_root(root)
    if isinstance(pack, ModelPack):
        return _pack_installed(pack, base)
    known = PACK_BY_KEY.get(str(pack))
    if known is not None:
        return _pack_installed(known, base)
    return _dir_has_entries(base / str(pack))


def installed_packs(root: Path | str | None = None) -> list[str]:
    """已安装的模型包 key 列表（按 :data:`PACKS` 顺序）。"""
    base = _base_root(root)
    return [pack.key for pack in PACKS if _pack_installed(pack, base)]


def missing_packs(root: Path | str | None = None) -> list[ModelPack]:
    """尚未安装的模型包（按 :data:`PACKS` 顺序）。"""
    base = _base_root(root)
    return [pack for pack in PACKS if not _pack_installed(pack, base)]


# --------------------------------------------------------------- 解压 --
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _safe_parts(name: str) -> tuple[str, ...]:
    """把 zip 内路径拆成安全分段；绝对路径 / 盘符 / ``..`` 一律拒绝。"""
    raw = name.replace("\\", "/")
    if not raw or raw.startswith("/") or _DRIVE_RE.match(raw):
        raise DownloadError(f"模型包内含非法路径：{name}")
    parts = tuple(part for part in raw.split("/") if part not in ("", "."))
    if not parts:
        raise DownloadError(f"模型包内含非法路径：{name}")
    for part in parts:
        if part == ".." or ":" in part:
            raise DownloadError(f"模型包内含非法路径：{name}")
    return parts


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _replace_dir(source: Path, dest: Path) -> None:
    """用 ``source`` 替换 ``dest``：先挪走旧目录，再 rename，失败则恢复。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if dest.exists():
        backup = dest.parent / f".{dest.name}.old-{uuid.uuid4().hex[:8]}"
        try:
            os.rename(dest, backup)
        except OSError as exc:
            raise DownloadError(f"安装模型失败：{exc}") from exc
    try:
        os.rename(source, dest)
    except OSError as exc:
        if backup is not None and backup.exists():
            try:
                os.rename(backup, dest)
            except OSError:
                pass
        raise DownloadError(f"安装模型失败：{exc}") from exc
    if backup is not None:
        _remove_path(backup)


def install_archive(archive: Path | str, root: Path | str | None = None) -> list[str]:
    """把模型 zip 安装到模型根目录，返回安装的 pack key 列表。

    只解压能识别的顶层目录（:data:`PACKS` 的 ``subdir``，如 ``ncnn/``、
    ``apisr/``）；先解到模型根目录里的临时目录，再逐个替换目标子目录。
    路径中包含绝对路径、盘符或 ``..`` 时直接拒绝整个包。
    """
    archive = Path(archive)
    if not archive.is_file():
        raise DownloadError(f"找不到模型包：{archive}")
    target = _base_root(root)
    target.mkdir(parents=True, exist_ok=True)

    recognized = {pack.subdir for pack in PACKS}
    staging = Path(tempfile.mkdtemp(prefix=".imgforge-install-", dir=str(target)))
    try:
        try:
            with zipfile.ZipFile(archive) as zf:
                plan: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
                for info in zf.infolist():
                    parts = _safe_parts(info.filename)
                    if info.is_dir():
                        continue
                    # 只保留「顶层目录 = 已知包目录」的文件，其余条目安全忽略。
                    if len(parts) < 2 or parts[0] not in recognized:
                        continue
                    plan.append((info, parts))
                if not plan:
                    raise DownloadError("压缩包里没有可识别的模型目录")

                for info, parts in plan:
                    dest = staging.joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as source, open(dest, "wb") as fh:
                        shutil.copyfileobj(source, fh, 1 << 20)
        except zipfile.BadZipFile as exc:
            raise DownloadError(f"模型包不是有效的 zip：{exc}") from exc

        keys: list[str] = []
        for pack in PACKS:
            staged = staging / pack.subdir
            if not _dir_has_entries(staged):
                continue
            _replace_dir(staged, target / pack.subdir)
            keys.append(pack.key)
        if not keys:
            raise DownloadError("压缩包里没有可识别的模型目录")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    clear_model_cache()
    return keys


# --------------------------------------------------------------- 下载 --
def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_target(root: Path | str | None) -> Path:
    """下载安装位置：显式 root，否则用户可写的默认模型目录。"""
    return Path(root) if root else default_models_root()


def download_pack(
    pack_or_key: ModelPack | str,
    root: Path | str | None = None,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    timeout: float = 60.0,
    url: str | None = None,
) -> list[str]:
    """下载并安装一个模型包，返回安装的 pack key 列表。

    * 流式下载到 ``%TEMP%``，先校验大小、再校验 sha256，最后调用
      :func:`install_archive`；
    * ``progress(downloaded, total)`` 用于进度条；
    * ``cancel()`` 返回真时抛 :class:`DownloadCancelled`；
    * ``url`` 可覆盖下载地址（测试 / 镜像用）。
    """
    pack = _resolve_pack(pack_or_key)
    if not pack.sha256:
        raise DownloadError(
            f"模型包校验值缺失，请重新打包并更新 {pack.key} 包的 sha256"
        )

    target = _download_target(root)
    target.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"imgforge-{pack.key}-", suffix=".zip.download"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        request = urllib.request.Request(
            url or pack_url(pack), headers={"User-Agent": "ImageForge"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                header_total = int(resp.headers.get("Content-Length") or 0)
                total = pack.size or header_total
                done = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if cancel and cancel():
                            raise DownloadCancelled("已取消")
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except urllib.error.HTTPError as exc:
            raise DownloadError(
                f"下载失败（HTTP {exc.code}），请稍后重试或手动下载后从文件安装"
            ) from exc
        except urllib.error.URLError as exc:
            raise DownloadError(f"网络不可用：{exc.reason}") from exc
        except TimeoutError as exc:
            raise DownloadError(f"下载超时：{exc}") from exc

        actual = tmp.stat().st_size
        if pack.size and actual != pack.size:
            raise DownloadError(
                f"模型包大小不正确（期望 {pack.size:,} 字节，实际 {actual:,} 字节），请重试"
            )
        digest = _sha256_of(tmp)
        if digest != pack.sha256.lower():
            raise DownloadError("模型包校验失败，文件可能不完整，请重试")
        if cancel and cancel():
            raise DownloadCancelled("已取消")
        return install_archive(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _legacy_ncnn_pack() -> ModelPack:
    """旧 API 的模块级常量可能在运行时被改写（测试 / 热更新），以常量为准。"""
    pack = PACK_BY_KEY["ncnn"]
    return ModelPack(
        key=pack.key,
        label=pack.label,
        archive=MODELS_ARCHIVE,
        tag=MODELS_RELEASE_TAG,
        size=int(MODELS_BYTES),
        sha256=str(MODELS_SHA256),
        subdir=pack.subdir,
        required=pack.required,
        note=pack.note,
    )


def download_models(
    root: Path | str | None = None,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    timeout: float = 60.0,
    url: str | None = None,
) -> Path:
    """（向后兼容）下载并安装 Real-CUGAN ncnn 模型包，返回模型根目录。

    等价于 ``download_pack("ncnn", root, ...)``，只是保留旧的 ``Path`` 返回值。
    """
    target = _download_target(root)
    download_pack(
        _legacy_ncnn_pack(),
        target,
        progress=progress,
        cancel=cancel,
        timeout=timeout,
        url=url,
    )
    return target
