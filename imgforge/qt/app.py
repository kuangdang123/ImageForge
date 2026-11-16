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
    app.setApplicationVersion("2.0.0")
    app.setWindowIcon(QIcon(str(ASSETS / "app.ico")))
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ImageForge", description="图片格式转换与多尺寸 ICO 生成")
    parser.add_argument("--theme", choices=["system", "light", "dark"], help="强制主题")
    parser.add_argument("--single", action="store_true", help="单实例（默认开启）")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

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
