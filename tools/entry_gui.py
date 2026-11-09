"""打包入口：给 PyInstaller 用，只负责开 GUI。

兼容两种运行方式：
- 源码树里直接 `python tools/entry_gui.py`；
- PyInstaller 打进 exe 后，入口模块作为脚本执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imgforge.gui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())