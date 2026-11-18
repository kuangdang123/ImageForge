"""单一版本号来源：界面标题、exe 版本信息、README 都读这里。"""

from __future__ import annotations

__version__ = "2.6.0"

# PyInstaller 的 VSVersionInfo 需要四段数字。
VERSION_TUPLE = tuple(int(part) for part in __version__.split(".")) + (0,)
