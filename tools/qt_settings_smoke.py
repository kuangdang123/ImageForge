"""验证 2.0 界面的设置真的会跨启动保存。

流程：起一个离屏 MainWindow → 改几项设置（格式 / 尺寸 / 元数据开关）→
等去抖写盘 → 关窗 → 重新加载，断言新窗口的控件状态与上次一致。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["IMAGEFORGE_HOME"] = str(ROOT / "tmp" / "settings_smoke")

from PySide6.QtWidgets import QApplication  # noqa: E402

from imgforge.settings import Settings  # noqa: E402
from imgforge.qt.app import build_app  # noqa: E402
from imgforge.qt.mainwindow import MainWindow  # noqa: E402
from imgforge.qt.theme import Theme  # noqa: E402


def pump(app: QApplication, ms: int) -> None:
    app.processEvents()
    import time

    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def open_window(app: QApplication, settings: Settings) -> MainWindow:
    theme = Theme(mode=str(settings["theme"] or "system"))
    theme.apply()
    window = MainWindow(theme, settings)
    window.show()
    pump(app, 120)
    return window


def main() -> int:
    home = Path(os.environ["IMAGEFORGE_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    target_file = home / "settings.json"
    target_file.unlink(missing_ok=True)

    app = build_app([])

    settings = Settings.load()
    window = open_window(app, settings)
    panel = window.settings_panel

    panel.target_group.set_value("jpg", emit=True)
    panel.sizes_grid.set_value([16, 32, 256])
    panel.fit_control.set_value("fill")
    panel.exif_row.set_value(False)
    panel.upscale_row.set_value(False)
    panel.commit()
    pump(app, 700)  # 等 350ms 去抖写盘

    assert target_file.exists(), "设置文件没有被写出来"
    import json

    raw = json.loads(target_file.read_text(encoding="utf-8"))
    assert raw["target"] == "jpg", raw["target"]
    assert raw["ico_sizes"] == [16, 32, 256], raw["ico_sizes"]
    assert raw["ico_fit"] == "fill", raw["ico_fit"]
    assert raw["keep_metadata"] is False
    assert raw["ico_upscale"] is False
    assert raw["quality_by_format"]["jpg"] == 92, raw["quality_by_format"]
    print("saved:", raw["target"], raw["ico_sizes"], raw["ico_fit"], raw["keep_metadata"])

    window.close()
    pump(app, 120)

    settings2 = Settings.load()
    window2 = open_window(app, settings2)
    panel2 = window2.settings_panel
    assert panel2.target_group.value() == "jpg", panel2.target_group.value()
    assert list(panel2.sizes_grid.value()) == [16, 32, 256], panel2.sizes_grid.value()
    assert panel2.fit_control.value() == "fill"
    assert panel2.exif_row.value() is False
    assert panel2.upscale_row.value() is False
    print("restored:", panel2.target_group.value(), panel2.sizes_grid.value(),
          panel2.fit_control.value(), panel2.exif_row.value())
    window2.close()
    pump(app, 100)
    print("settings persistence PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
