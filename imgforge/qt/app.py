"""PySide6 应用入口。"""

from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .. import single_instance
from ..settings import Settings
from ..version import __version__
from .mainwindow import MainWindow
from .theme import Theme

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ImageForge.Desktop.2")
    except Exception:  # noqa: BLE001
        pass


def build_app(argv: list[str]) -> QApplication:
    # Qt 6 自带 per-monitor DPI 感知；自己再设一次会被系统拒绝
    # （日志里会出现 SetProcessDpiAwarenessContext() failed）。
    _set_app_id()
    app = QApplication(argv)
    app.setApplicationName("ImageForge")
    app.setOrganizationName("ImageForge")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ASSETS / "app.ico")))
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    return app


def _run_selftest() -> int:
    """不打开界面的自检：确认超分权重可用并能真的推理一张小图。

    打包成 windowed exe 后没有控制台，所以结果同时写到
    ``%TEMP%\\ImageForge-selftest.txt`` 并以返回码表示成败（0 成功）。
    """
    import tempfile
    import traceback

    from PIL import Image

    from .. import upscale

    lines: list[str] = [f"ImageForge {__version__} self-test"]
    ok = True
    try:
        exe = upscale.ncnn_exe()
        lines.append(f"ncnn exe: {exe} exists={exe.exists()}")
        ok = ok and exe.exists()
        root = upscale.weights_root()
        lines.append(f"weights: {root} exists={root.is_dir()}")
        ok = ok and root.is_dir()
        with tempfile.TemporaryDirectory(prefix="imageforge-selftest-") as tmp:
            src = Path(tmp) / "check.png"
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for x in range(64):
                for y in range(64):
                    if (x + y) % 2 == 0:
                        image.putpixel((x, y), (40, 120, 220, 255))
            image.save(src)
            result = upscale.upscale_file(
                src,
                upscale.UpscaleOptions(model_id="cugan-se", device="cpu", scale=2, denoise=0),
                out_root=Path(tmp) / "out",
            )
            lines.append(
                f"upscale: ok={result.ok} size={result.width}x{result.height} "
                f"seconds={result.seconds:.1f} message={result.message}"
            )
            ok = ok and result.ok and (result.width, result.height) == (128, 128)
            if result.warnings:
                lines.append("warnings: " + " / ".join(result.warnings))
    except Exception:  # noqa: BLE001 - 自检要报告任何异常而不是弹窗
        lines.append(traceback.format_exc())
        ok = False
    lines.append("result: " + ("OK" if ok else "FAILED"))
    report = Path(tempfile.gettempdir()) / "ImageForge-selftest.txt"
    try:
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    print("\n".join(lines))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ImageForge", description="图片格式转换与多尺寸 ICO 生成")
    parser.add_argument("--theme", choices=["system", "light", "dark"], help="强制主题")
    parser.add_argument("--single", action="store_true", help="单实例（默认开启）")
    parser.add_argument("--check", action="store_true", help="自检超分权重并退出（不打开界面）")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.check:
        return _run_selftest()

    if not single_instance.acquire():
        single_instance.focus_existing()
        return 0

    app = build_app([sys.argv[0], *qt_args])
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    settings = Settings.load()
    if args.theme:
        settings["theme"] = args.theme
    theme = Theme(mode=str(settings["theme"] or "system"))
    theme.apply()
    window = MainWindow(theme, settings)
    window.show()
    return app.exec()
