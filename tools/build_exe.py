"""用 PyInstaller 打一个轻量单文件 exe（发布用）。

    python tools/build_exe.py            # 输出 dist/ImageForge.exe
    python tools/build_exe.py --onedir   # 只产出文件夹版本，启动更快

产物不含 Python 环境；tkinterdnd2 的 tkdnd 二进制由 hook 自动收集。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
VERSION = "1.0.0"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404B0',
          [
            StringStruct('CompanyName', 'ImageForge'),
            StringStruct('FileDescription', 'ImageForge 图片格式转换'),
            StringStruct('FileVersion', '1.0.0.0'),
            StringStruct('InternalName', 'ImageForge'),
            StringStruct('LegalCopyright', ''),
            StringStruct('OriginalFilename', 'ImageForge.exe'),
            StringStruct('ProductName', 'ImageForge'),
            StringStruct('ProductVersion', '1.0.0.0'),
            StringStruct('Comments', '图片格式转换与多尺寸 ICO 生成'),
          ],
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [2052, 1200])]),
  ]
)
"""

EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib", "IPython", "pytest",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "torch", "cv2", "pydantic",
]


def main() -> int:
    onedir = "--onedir" in sys.argv
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    version_file.write_text(VERSION_INFO, encoding="utf-8")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        "--windowed",
        "--name", "ImageForge",
        "--icon", str(ROOT / "imgforge" / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'imgforge' / 'assets'}{';'}imgforge/assets",
        "--collect-all", "tkinterdnd2",
        "--version-file", str(version_file),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
    ]
    for name in EXCLUDES:
        args.extend(["--exclude-module", name])
    args.append(str(ROOT / "tools" / "entry_gui.py"))

    print("PyInstaller:", " ".join(args[2:]))
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败", file=sys.stderr)
        return result.returncode
    exe = DIST / ("ImageForge/ImageForge.exe" if onedir else "ImageForge.exe")
    if exe.exists():
        print(f"完成：{exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())