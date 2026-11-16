"""超分页冒烟：离屏跑一次真实 Real-CUGAN 放大，验证 UI → 队列 → 输出。

用法：python tools/qt_upscale_smoke.py [--device cpu]
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["IMAGEFORGE_HOME"] = str(ROOT / "tmp" / "settings_upscale")
    settings_file = Path(os.environ["IMAGEFORGE_HOME"]) / "settings.json"
    if settings_file.exists():
        settings_file.unlink()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from imgforge.qt.app import build_app
    from imgforge.qt.mainwindow import MainWindow
    from imgforge.qt.theme import Theme
    from imgforge.settings import Settings

    app = build_app(["qt_upscale_smoke"])
    settings = Settings.load()
    theme = Theme("light")
    theme.apply()
    window = MainWindow(theme, settings)
    window.show()

    out_dir = ROOT / "tmp" / "upscale_out"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings["out_mode"] = "folder"
    settings["out_dir"] = str(out_dir)
    settings["keep_tree"] = False

    sample = ROOT / "samples" / "tall.png"
    started = time.time()
    window.add_paths([sample])
    state = {"phase": "probe"}

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    pump(1.5)
    info = window.model.items[0].info
    print(f"probe: {getattr(info, 'width', 0)}x{getattr(info, 'height', 0)}")

    window.nav.set_value("upscale", emit=True)
    panel = window.upscale_panel
    panel._building = True
    panel.model_group.set_value("cugan-se")
    panel.device.set_value("cpu")
    panel.mode.set_value("scale")
    panel._rebuild_scale_options(keep=2)
    panel.scale_group.set_value("2")
    panel._rebuild_denoise_options(keep=0)
    panel._building = False
    panel.refresh()
    panel._emit()
    print(f"options: {panel.options()}")

    window.start_upscale()
    while window._upscale_worker is not None and time.time() - started < 180:
        app.processEvents()
        time.sleep(0.02)
    pump(0.3)

    item = window.model.items[0]
    result = item.result
    print(f"status={item.status} ok={getattr(result, 'ok', None)} dest={getattr(result, 'dest', None)}")
    print(f"size={getattr(result, 'width', 0)}x{getattr(result, 'height', 0)} seconds={getattr(result, 'seconds', 0):.2f}")
    print(f"dest exists: {Path(result.dest).exists() if result and result.dest else False}")
    print(f"elapsed: {time.time() - started:.1f}s")
    window.close()
    return 0 if (result is not None and result.ok and result.dest and Path(result.dest).exists()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
