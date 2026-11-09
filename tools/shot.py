"""开发用截图工具：起一个真实窗口、可选塞入样例文件，然后截屏保存。

    python tools/shot.py dark tmp_icons/shot_dark.png tmp_demo/logo_square.png ...
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from imgforge.gui import App  # noqa: E402
from imgforge.settings import Settings  # noqa: E402
from imgforge.theme import enable_dpi_awareness  # noqa: E402


def window_rect(app) -> tuple[int, int, int, int]:
    """整窗（含标题栏）的物理像素矩形。"""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom
    return app.winfo_rootx(), app.winfo_rooty(), app.winfo_rootx() + app.winfo_width(), app.winfo_rooty() + app.winfo_height()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "light"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "tmp_icons" / "shot.png")
    files = [Path(p) for p in sys.argv[3:]]

    (ROOT / "tmp" / "settings" / "settings.json").unlink(missing_ok=True)
    enable_dpi_awareness()
    settings = Settings.load()
    settings["theme"] = mode
    settings["target"] = "ico"
    settings["window"] = "1120x680"
    settings["out_mode"] = "same"

    app = App(settings)
    app.update()
    if files:
        app.add_paths(files)

    def shoot() -> None:
        app.update_idletasks()
        app.update()
        x, y, right, bottom = window_rect(app)
        image = ImageGrab.grab(bbox=(x, y, right, bottom), all_screens=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        print(f"saved {out} {image.size} rect={(x, y, right, bottom)}")
        app.destroy()

    app.after(1800, shoot)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())