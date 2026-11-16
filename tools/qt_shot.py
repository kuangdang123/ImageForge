"""用真实 Windows 平台渲染界面并截图（能看到中文字体与 DPI 效果）。

用法：
    python tools/qt_shot.py [light|dark] [out.png]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings"))
os.environ.pop("QT_QPA_PLATFORM", None)

SAMPLES = ROOT / "samples"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    theme_name = "light"
    if args and args[0] in ("light", "dark"):
        theme_name = args.pop(0)
    out = ROOT / "tmp" / "shots" / f"qt_native_{theme_name}.png"
    if args and not args[0].startswith("--"):
        out = Path(args.pop(0))
    mode = "main"
    if "--picker" in args:
        mode = "picker"
        out = ROOT / "tmp" / "shots" / f"qt_native_picker_{theme_name}.png"
    if "--preview" in args:
        mode = "preview"
        out = ROOT / "tmp" / "shots" / f"qt_native_preview_{theme_name}.png"
    if "--convert" in args:
        mode = "convert"
        out = ROOT / "tmp" / "shots" / f"qt_native_convert_{theme_name}.png"
    if "--upscale" in args:
        mode = "upscale"
        out = ROOT / "tmp" / "shots" / f"qt_native_upscale_{theme_name}.png"
    if "--upscale-done" in args:
        mode = "upscale-done"
        out = ROOT / "tmp" / "shots" / f"qt_native_upscale_done_{theme_name}.png"
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    settings_file = Path(os.environ["IMAGEFORGE_HOME"]) / "settings.json"
    settings_file.unlink(missing_ok=True)

    from imgforge.settings import Settings
    from imgforge.qt.app import build_app
    from imgforge.qt.mainwindow import MainWindow
    from imgforge.qt.theme import Theme

    app = build_app([sys.argv[0]])
    settings = Settings.load()
    settings["theme"] = theme_name
    settings["target"] = "ico"
    if "--convert" in args:
        out_dir = ROOT / "tmp" / "shot_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        settings["out_mode"] = "folder"
        settings["out_dir"] = str(out_dir)
        settings["keep_tree"] = False
    else:
        settings["out_mode"] = "same"
    theme = Theme(mode=theme_name)
    theme.apply()
    window = MainWindow(theme, settings)
    window.resize(1180, 800)
    window.show()

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
        if window.isVisible():
            break

    files = sorted(p for p in SAMPLES.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif", ".tif", ".tiff", ".avif"))
    window.add_paths(files)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and window._pending_probe:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(40):
        app.processEvents()
        time.sleep(0.01)

    def settle(seconds: float = 0.6) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    if mode == "convert":
        window.start_conversion()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and window._worker is not None:
            app.processEvents()
            time.sleep(0.01)
        settle(0.6)
        window.grab().save(str(out))
    elif mode.startswith("upscale"):
        window.nav.set_value("upscale", emit=True)
        settle(0.3)
        if not window.upscale_panel.is_ready():
            out = ROOT / "tmp" / "shots" / f"qt_native_models_missing_{theme_name}.png"
        if mode == "upscale-done":
            window.clear_all()
            window.add_paths([SAMPLES / "tall.png"])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and window._pending_probe:
                app.processEvents()
                time.sleep(0.01)
            tout = ROOT / "tmp" / "shot_out"
            tout.mkdir(parents=True, exist_ok=True)
            window.settings["out_mode"] = "folder"
            window.settings["out_dir"] = str(tout)
            window.upscale_panel.device.set_value("cpu", emit=True)
            window.upscale_panel.model_box.set_value("cugan-se", emit=True)
            settle(0.2)
            window.start_upscale()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and window._upscale_worker is not None:
                app.processEvents()
                time.sleep(0.02)
            settle(0.8)
        window.grab().save(str(out))
    elif mode == "preview":
        from imgforge.qt.preview import PreviewDialog

        row = next((r for r, it in enumerate(window.model.items) if it.status in ("idle", "done")), 0)
        dialog = PreviewDialog(window, theme, window.model.items[row])
        dialog.resize(900, 680)
        dialog.show()
        settle(1.0)
        dialog.grab().save(str(out))
        dialog.close()
    elif mode == "picker":
        from imgforge.qt.picker import FolderPicker

        picker = FolderPicker(window, theme, SAMPLES, existing=set(), recursive=False)
        picker.show()
        settle(1.2)
        for row in (0, 2):
            picker.list.set_row_checked(row, True)
        picker._update_footer()
        settle(0.3)
        picker.grab().save(str(out))
        picker.close()
    else:
        if "--compare" in args:
            # 取消几个尺寸，检查选中/未选中对比
            window.settings_panel.sizes_grid.set_value([16, 32, 256])
            window.settings_panel.sizes_grid.update()
            settle(0.4)
        settle(0.4)
        window.grab().save(str(out))
    print("saved", out, out.stat().st_size if out.exists() else "missing")
    if window.model.items:
        first = window.model.items[0]
        print("first", first.path.name, first.status, first.meta_text(theme))
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
