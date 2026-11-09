"""冒烟：文件夹浏览窗口 → 多选 → 加入队列 → 自动转换。

用法：python tools\\picker_smoke.py [light|dark]
截图落在 tmp\\shots\\picker_*.png，转换结果落在 tmp\\gui_out\\。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings"))
sys.path.insert(0, str(ROOT))

from imgforge.gui import App  # noqa: E402
from imgforge.picker import FolderPicker  # noqa: E402
from imgforge.settings import Settings  # noqa: E402
from imgforge.theme import enable_dpi_awareness  # noqa: E402

SAMPLES = ROOT / "samples"
SHOTS = ROOT / "tmp" / "shots"
OUT = ROOT / "tmp" / "gui_out"


def grab(window, path: Path) -> None:
    window.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    ImageGrab.grab(bbox=bbox, all_screens=True).save(path)
    print(f"  shot {path.relative_to(ROOT)} {bbox}")


def pump(root, predicate, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    (ROOT / "tmp" / "settings" / "settings.json").unlink(missing_ok=True)
    enable_dpi_awareness()
    SHOTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.ico"):
        stale.unlink()

    mode = sys.argv[1] if len(sys.argv) > 1 else "light"
    settings = Settings.load()
    settings["theme"] = mode
    settings["target"] = "ico"
    settings["out_mode"] = "folder"
    settings["out_dir"] = str(OUT)
    settings["keep_tree"] = False

    app = App(settings)
    app.update()

    existing = {str((SAMPLES / "photo.jpg").resolve())}
    picker = FolderPicker(app, app.theme, SAMPLES, existing=existing, recursive=False)
    ok = pump(app, lambda: not picker._scanning and not picker._building and bool(picker._rows))
    print(f"scan ok={ok} rows={len(picker._rows)} visible={len(picker._visible)}")
    grab(picker, SHOTS / f"picker_{mode}.png")

    choosable = [path for path in picker._build_paths if not picker._rows[path].taken]
    for path in choosable[:2]:
        picker._set_checked(path, True)
    picker._update_footer()
    app.update()
    print(f"checked={len(picker._checked)} confirm_enabled={picker.confirm_btn._enabled}")
    grab(picker, SHOTS / f"picker_selected_{mode}.png")

    picker._select_all()
    app.update()
    print(f"select_all={len(picker._checked)} of {len(choosable)} choosable")

    picker._placeholder_on = False
    picker.search_var.set("logo")
    filtered = pump(app, lambda: picker._search_job is None and len(picker._visible) == 2, timeout=5)
    print(f"filter ok={filtered} visible={[path.name for path in picker._visible]}")
    picker.search_var.set("")
    restored = pump(app, lambda: picker._search_job is None and len(picker._visible) == 5, timeout=5)
    print(f"filter cleared={restored}")

    picker._confirm(False)
    picked = list(picker.result)
    print("result:", [path.name for path in picked])
    if not picked:
        print("FAIL: 没有选中任何图片")
        return 1

    app.add_paths(picked)
    app._pending_autostart = True
    pumped = pump(app, lambda: not (app._probe_thread and app._probe_thread.is_alive()) and not app._probe_pending)
    print(f"probe ok={pumped} items={len(app.items)}")
    app._try_autostart()
    finished = pump(app, lambda: app._done + app._failed >= len(picked) and not app._converting)
    print(f"convert finished={finished} done={app._done} failed={app._failed}")
    app.update()
    grab(app, SHOTS / f"picker_after_convert_{mode}.png")

    produced = sorted(OUT.glob("*.ico"))
    print("outputs:", [path.name for path in produced])
    ok = finished and app._done >= len(picked) and bool(produced) and filtered and restored

    # ---- 阶段 2：真实模态路径（app._open_picker + wait_window + 自动开始）----
    state = {"opened": False, "closed": False}

    def drive_modal() -> None:
        picker2 = getattr(app, "_picker", None)
        if picker2 is None:
            app.after(80, drive_modal)
            return
        if picker2._scanning or picker2._building or not picker2._rows:
            app.after(80, drive_modal)
            return
        free = [path for path in picker2._build_paths if not picker2._rows[path].taken]
        print(f"modal free={len(free)} rows={len(picker2._rows)}")
        if free:
            picker2._set_checked(free[0], True)
            picker2._update_footer()
        grab(picker2, SHOTS / f"picker_modal_{mode}.png")
        picker2._confirm(True)

    def open_modal() -> None:
        state["opened"] = True
        app.after(600, drive_modal)
        app.after(25000, lambda: state["closed"] or (app._picker and app._picker._cancel()))
        app._open_picker(SAMPLES)
        state["closed"] = True

    app.after(200, open_modal)
    modal_closed = pump(app, lambda: state["closed"], timeout=20)
    print(f"modal opened={state['opened']} closed={modal_closed} picker={app._picker!r}")

    before = app._done
    seen = []
    end = time.time() + 20
    finished2 = False
    while time.time() < end:
        app.update()
        state_now = (app._done, app._failed, app._converting)
        if not seen or seen[-1][-1] != state_now:
            seen.append(state_now + (time.time(),))
        if app._done > before and not app._converting:
            finished2 = True
            break
        time.sleep(0.02)
    print(f"autostart trace before={before} seen={[(d, f, c) for d, f, c, _ in seen[:6]]}")
    print(f"autostart finished={finished2} done={app._done} failed={app._failed} items={len(app.items)}")
    grab(app, SHOTS / f"picker_modal_done_{mode}.png")
    produced = sorted(OUT.glob("*.ico"))
    print("outputs:", [path.name for path in produced])
    ok = ok and modal_closed and finished2 and len(app.items) == 5 and bool(produced)
    print("RESULT", "PASS" if ok else "FAIL")
    app.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())