"""打包 APISR ONNX 模型为 zip（供 GitHub release 资产按需下载），并输出 sha256。

用法::

    python tools/pack_models.py              # 打包并打印 size / sha256
    python tools/pack_models.py --verify     # 打包后立刻安装到临时目录验证
    python tools/pack_models.py --src PATH   # 指定源目录（默认 tmp/onnx_final）

确定性打包：文件按名字排序、zip 内时间戳固定为 (2020, 1, 1, 0, 0, 0)、
ZIP_DEFLATED，同一批源文件在任何机器上打出的 sha256 都一致。

zip 内结构为 ``apisr/grl4.onnx``、``apisr/rrdb4.onnx`` …，安装到模型根目录后
即为 ``<models>/apisr/*.onnx``。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "tmp" / "onnx_final"
OUT_DIR = ROOT / "dist" / "models"
ARCHIVE = "apisr-onnx-models.zip"
OUT = OUT_DIR / ARCHIVE
SUBDIR = "apisr"
TAG = "models-v2"
#: 确定性打包用的固定时间戳（zip 最小合法年份是 1980，这里用 2020 便于识别）。
FIXED_DATE_TIME = (2020, 1, 1, 0, 0, 0)
COMPRESS_LEVEL = 6


def _ensure_utf8_stdout() -> None:
    """输出被重定向时用 UTF-8，避免中文按 GBK 写出后变成乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(src: Path) -> tuple[Path, list[Path]]:
    """返回 (文件所在目录, 按 posix 相对路径排序的文件列表)。

    默认源目录是 ``tmp/onnx_final``（里面含 ``apisr/``）；也允许直接把
    ``--src`` 指到 ``apisr`` 目录本身。
    """
    if not src.exists():
        raise SystemExit(f"错误：找不到源目录 {src}")
    folder = src / SUBDIR if (src / SUBDIR).is_dir() else src
    files = sorted(
        (path for path in folder.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(folder).as_posix(),
    )
    if not files:
        raise SystemExit(f"错误：{folder} 里没有文件")
    if not any(path.suffix.lower() == ".onnx" for path in files):
        raise SystemExit(f"错误：{folder} 里没有 .onnx 模型文件")
    return folder, files


def _write_zip(folder: Path, files: list[Path], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL
    ) as zf:
        for path in files:
            arcname = f"{SUBDIR}/{path.relative_to(folder).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 固定权限位，避免不同机器的 umask 影响字节。
            info.external_attr = 0o600 << 16
            with open(path, "rb") as src, zf.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)


def _print_snippet(size: int, sha256: str) -> None:
    print()
    print("可直接粘贴到 imgforge/upscale/download.py 的常量：")
    print()
    print(f'APISR_ARCHIVE = "{ARCHIVE}"')
    print(f'APISR_TAG = "{TAG}"')
    print(f"APISR_BYTES = {size}")
    print(f'APISR_SHA256 = "{sha256}"')
    print()
    print("PACKS 里的 apisr 项：")
    print()
    print("    ModelPack(")
    print('        key="apisr",')
    print('        label="APISR 模型",')
    print("        archive=APISR_ARCHIVE,")
    print("        tag=APISR_TAG,")
    print("        size=APISR_BYTES,")
    print("        sha256=APISR_SHA256,")
    print('        subdir="apisr",')
    print("        required=False,")
    print('        note="APISR 动漫 4× 超分 ONNX 模型（可选）",')
    print("    ),")


def _verify(archive: Path) -> int:
    """装到临时目录，断言 installed() 为真，再清理。"""
    sys.path.insert(0, str(ROOT))
    from imgforge.upscale import download

    try:
        pack = download.PACK_BY_KEY[SUBDIR]
    except KeyError:
        print(f"verify: FAIL —— download.PACKS 里还没有 {SUBDIR!r} 包", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="imgforge-pack-verify-") as tmp:
        root = Path(tmp)
        keys = download.install_archive(archive, root)
        if SUBDIR not in keys:
            print(f"verify: FAIL —— install_archive 返回 {keys!r}", file=sys.stderr)
            return 1
        if not download.installed(pack, root):
            print("verify: FAIL —— 安装后 installed() 仍为 False", file=sys.stderr)
            return 1
        onnx_files = sorted(path.name for path in (root / SUBDIR).glob("*.onnx"))
        if not onnx_files:
            print("verify: FAIL —— 安装目录里没有 .onnx 文件", file=sys.stderr)
            return 1
    print(f"verify: OK —— 已解压并通过 installed() 检查（{', '.join(onnx_files)}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="打包 APISR ONNX 模型 zip（确定性打包）")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="源目录（默认 tmp/onnx_final）")
    parser.add_argument("--out", type=Path, default=OUT, help=f"输出 zip（默认 {OUT}）")
    parser.add_argument("--verify", action="store_true", help="打包后立即安装到临时目录验证")
    args = parser.parse_args(argv)

    src = args.src if args.src.is_absolute() else (Path.cwd() / args.src)
    out = args.out if args.out.is_absolute() else (Path.cwd() / args.out)

    folder, files = _collect(src)
    _write_zip(folder, files, out)
    size = out.stat().st_size
    sha256 = _sha256_of(out)

    print(f"src   : {folder}")
    print(f"zip   : {out}")
    print(f"files : {len(files)}")
    for path in files:
        print(f"  {SUBDIR}/{path.relative_to(folder).as_posix()}  ({path.stat().st_size:,} bytes)")
    print(f"size  : {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
    print(f"sha256: {sha256}")
    _print_snippet(size, sha256)

    if args.verify:
        return _verify(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
