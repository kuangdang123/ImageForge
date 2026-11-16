"""ImageForge 2.0 源码启动入口（pythonw app2.pyw）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imgforge.qt import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
