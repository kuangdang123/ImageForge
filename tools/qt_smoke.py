"""Qt 界面冒烟：离屏起窗 → 加图 → 等探测 → 转换 → 截图。

用法：
    python tools/qt_smoke.py [light|dark] [--out tmp/qt_out] [--shot tmp/shots]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings"))

SAMPLES = ROOT / "samples"


def pump(app, seconds: float = 0.2, until=None, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    end = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
        if until is not None and until():
            return True
        if until is None and time.monotonic() >= end:
            return True
    return until() if until is not None else False


def main() -> int:
    theme_name = "light"
    out_dir = ROOT / "tmp" / "qt_out"
    shot_dir = ROOT / "tmp" / "shots"
    args = sys.argv[1:]
    if args and args[0] in ("light", "dark"):
        theme_name = args[0]
        args = args[1:]
    i = 0
    while i < len(args):
        if args[i] == "--out":
            out_dir = Path(args[i + 1])
            i += 2
        elif args[i] == "--shot":
            shot_dir = Path(args[i + 1])
            i += 2
        else:
            i += 1
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    shot_dir = shot_dir if shot_dir.is_absolute() else ROOT / shot_dir

    settings_file = Path(os.environ["IMAGEFORGE_HOME"]) / "settings.json"
    settings_file.unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.ico"):
        stale.unlink(missing_ok=True)

    from imgforge.settings import Settings
    from imgforge.qt.app import build_app
    from imgforge.qt.mainwindow import MainWindow
    from imgforge.qt.theme import Theme

    app = build_app([sys.argv[0]])
    settings = Settings.load()
    settings["theme"] = theme_name
    settings["target"] = "ico"
    settings["out_mode"] = "folder"
    settings["out_dir"] = str(out_dir)
    settings["keep_tree"] = False
    theme = Theme(mode=theme_name)
    theme.apply()
    window = MainWindow(theme, settings)
    window.resize(1180, 720)
    window.show()
    pump(app, 0.3)
    window.grab().save(str(shot_dir / f"qt_empty_{theme_name}.png"))

    files = sorted(SAMPLES.glob("*"))
    files = [p for p in files if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif", ".tif", ".tiff", ".avif")]
    window.add_paths(files)
    ok = pump(app, until=lambda: window._pending_probe == 0, timeout=30)
    print("probe_done", ok, "items", len(window.model.items))
    window.file_panel.list.viewport().update()
    pump(app, 0.4)
    window.grab().save(str(shot_dir / f"qt_ready_{theme_name}.png"))

    window.start_conversion()
    ok = pump(app, until=lambda: window._worker is None, timeout=60)
    print("convert_done", ok)
    pump(app, 0.4)
    window.grab().save(str(shot_dir / f"qt_done_{theme_name}.png"))

    outputs = sorted(p.name for p in out_dir.glob("*.ico"))
    print("outputs", outputs)
    for item in window.model.items:
        print(" -", item.path.name, item.status, item.result.message if item.result and not item.result.ok else "")

    # 预览窗口
    if window.model.items:
        from imgforge.qt.preview import PreviewDialog

        row = next((r for r, it in enumerate(window.model.items) if it.status == "done"), 0)
        dialog = PreviewDialog(window, theme, window.model.items[row])
        dialog.resize(880, 660)
        dialog.show()
        pump(app, 0.8)
        dialog.grab().save(str(shot_dir / f"qt_preview_{theme_name}.png"))
        print("preview_shot", row)
        dialog.close()

    # 挑图窗口
    from imgforge.qt.picker import FolderPicker

    picker = FolderPicker(window, theme, SAMPLES, existing=set(window.model.paths()), recursive=False)
    picker.show()
    pump(app, 1.2)
    picker.list.set_row_checked(0, True)
    picker.list.set_row_checked(1, True)
    picker._update_footer()
    pump(app, 0.2)
    picker.grab().save(str(shot_dir / f"qt_picker_{theme_name}.png"))
    print("picker", picker.list.count(), "checked", len(picker.list.checked_rows()))
    picker.close()

    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
