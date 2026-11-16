"""ImageForge 2.0 的 PyInstaller 入口（Qt 版）。

打包时由 tools/build_exe.py 作为脚本目标；直接运行没有意义。
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

from imgforge.qt.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
