"""离屏验证视频页：探测 → 选格式 → 转换 → 结果状态。

用法：python tools/qt_video_smoke.py [light|dark]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "samples" / "clip.mp4"
OUT_DIR = ROOT / "tmp" / "video_out"
HOME = ROOT / "tmp" / "settings_video"


def pump(app, page, seconds: float = 0.0, *, until=None) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until is not None else True


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from imgforge import video
    from imgforge.qt.app import build_app
    from imgforge.qt.mainwindow import MainWindow
    from imgforge.qt.theme import Theme
    from imgforge.settings import Settings

    mode = sys.argv[1] if len(sys.argv) > 1 else "light"
    if not SAMPLE.is_file():
        print(f"缺少样例视频：{SAMPLE}")
        return 1
    if not video.ffmpeg_available():
        print("没有找到 ffmpeg，跳过视频冒烟")
        return 0

    import shutil

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True)
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "settings.json").unlink(missing_ok=True)
    os.environ.setdefault("IMAGEFORGE_HOME", str(HOME))
    os.environ.setdefault("IMAGEFORGE_MODELS_DIR", str(ROOT / "imgforge" / "upscale" / "weights"))

    app: QApplication = build_app([sys.argv[0], "-platform", "offscreen"])
    settings = Settings.load()
    settings["out_mode"] = "folder"
    settings["out_dir"] = str(OUT_DIR)
    theme = Theme(mode=mode)
    theme.apply()
    window = MainWindow(theme, settings)
    window.resize(1180, 800)
    window.show()

    page = window.video_page
    window.nav.set_value("video", emit=True)
    app.processEvents()
    checks: list[tuple[str, bool]] = []
    checks.append(("导航切到视频页", window.content_stack.currentWidget() is page))
    checks.append(("图片命令栏隐藏", not window.command_widget.isVisible()))

    page.add_paths([SAMPLE])
    pump(app, page, 30, until=lambda: page._pending_probe == 0)
    item = page.model.items[0] if page.model.items else None
    checks.append(("探测完成", item is not None and item.status == "idle"))
    checks.append(("规格文案", bool(item and "320×240" in item.meta_override)))
    checks.append(("缩略图", bool(item and item.thumb is not None)))
    checks.append(("开始按钮可用", page.start_btn.isEnabled()))

    page.panel.target_group.set_value("gif", emit=True)
    app.processEvents()
    checks.append(("GIF 隐藏质量滑杆", not page.panel.quality_row.isVisible()))
    checks.append(("GIF 显示帧率", page.panel.gif_box.isVisible()))

    page.start()
    pump(app, page, 120, until=lambda: not page.is_running())
    outputs = sorted(OUT_DIR.glob("*.gif"))
    checks.append(("转换完成", item is not None and item.status == "done"))
    checks.append(("产出 GIF", bool(outputs)))
    checks.append(("进度满格", page.progress._value >= 0.999))
    checks.append(("状态文案", "完成" in page.status_label.text()))

    # 再切回图片页，确认命令栏与底栏恢复
    window.nav.set_value("convert", emit=True)
    app.processEvents()
    checks.append(("切回图片页", window.content_stack.currentWidget() is window.image_page))
    checks.append(("命令栏恢复", window.command_widget.isVisible()))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"outputs: {[p.name for p in outputs]}")
    print("RESULT:", "PASS" if not failed else "FAIL " + ", ".join(failed))
    window.close()
    app.processEvents()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
