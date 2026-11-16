"""ImageForge 2.0（PySide6）——现代化界面。

与 1.0 的 tkinter 界面共用同一套转换引擎（imgforge.converter / imgforge.ico），
所以格式能力、ICO 编码细节、命名冲突策略完全一致。
"""

from __future__ import annotations

import sys

from .app import main

__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
