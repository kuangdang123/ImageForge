"""python -m imgforge [命令] [参数]

不带参数时打开图形界面，带参数时转交命令行处理。
"""

from __future__ import annotations

import sys

from . import cli

if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli.main(sys.argv[1:]))
    from .gui import main as gui_main

    raise SystemExit(gui_main())