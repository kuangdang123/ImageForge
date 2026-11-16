"""打包 Real-CUGAN ncnn 模型为 zip（供 release 资产下载），并输出 sha256。"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "imgforge" / "upscale" / "weights"
OUT_DIR = ROOT / "dist" / "models"
OUT = OUT_DIR / "realcugan-ncnn-models.zip"


def main() -> int:
    if not (SRC / "ncnn").is_dir():
        print(f"错误：找不到模型目录 {SRC / 'ncnn'}", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.unlink(missing_ok=True)

    files = sorted(p for p in SRC.rglob("*") if p.is_file())
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            arcname = path.relative_to(SRC).as_posix()
            zf.write(path, arcname)

    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"zip   : {OUT}")
    print(f"files : {len(files)}")
    print(f"size  : {OUT.stat().st_size} bytes ({OUT.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
