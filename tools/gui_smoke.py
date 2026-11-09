"""GUI 冒烟测试：真的走一遍「加入 → 探测 → 转换 → 预览」并截图。

    python tools/gui_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from imgforge.gui import App, PreviewWindow  # noqa: E402
from imgforge.settings import Settings  # noqa: E402
from imgforge.theme import enable_dpi_awareness  # noqa: E402


def window_rect(app) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def grab(widget, path: Path) -> None:
    widget.update_idletasks()
    widget.update()
    x, y, right, bottom = window_rect(widget)
    image = ImageGrab.grab(bbox=(x, y, right, bottom), all_screens=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    print(f"saved {path} {image.size}")


def pump(app, predicate, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        if predicate():
            return True
        time.sleep(0.03)
    return False


def main() -> int:
    (ROOT / "tmp" / "settings" / "settings.json").unlink(missing_ok=True)
    enable_dpi_awareness()
    demo = ROOT / "samples"
    out_dir = ROOT / "tmp" / "gui_out"
    shots = ROOT / "tmp" / "shots"
    if out_dir.exists():
        for stale in out_dir.glob("*.ico"):
            stale.unlink()
    settings = Settings.load()
    settings["theme"] = "light"
    settings["target"] = "ico"
    settings["window"] = "1120x680"
    settings["out_mode"] = "folder"
    settings["out_dir"] = str(out_dir)
    settings["keep_tree"] = False

    app = App(settings)
    app.update()
    app.add_paths([demo / "logo_square.png", demo / "wide_banner.png", demo / "photo.jpg"])
    ok = pump(app, lambda: all(item.status != "reading" for item in app.items))
    print("probe done:", ok, [item.status for item in app.items])
    grab(app, shots / "smoke_ready.png")

    app._start_conversion()
    ok = pump(app, lambda: not app._converting, timeout=60)
    print("convert done:", ok, "done=", app._done, "failed=", app._failed)
    pump(app, lambda: False, timeout=0.4)
    grab(app, shots / "smoke_done.png")

    produced = sorted(p.name for p in out_dir.glob("*.ico"))
    print("outputs:", produced)

    item = next((i for i in app.items if i.status == "done"), None)
    if item is not None:
        preview = PreviewWindow(app, item)
        pump(app, lambda: False, timeout=0.8)
        grab(preview, shots / "smoke_preview.png")
        preview.destroy()

    app.destroy()
    return 0 if app._failed == 0 and produced else 1


if __name__ == "__main__":
    raise SystemExit(main())