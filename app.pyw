"""ImageForge 图形界面入口（用 pythonw.exe 运行时不弹控制台窗口）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imgforge.gui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())