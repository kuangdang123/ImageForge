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


def _run_selftest(require_models: bool = False) -> int:
    """不打开界面的自检：确认超分权重可用并能真的推理一张小图。

    打包成 windowed exe 后没有控制台，所以结果同时写到
    ``%TEMP%\\ImageForge-selftest.txt`` 并以返回码表示成败（0 成功）。
    模型按需下载的版本里，未安装模型不算失败（除非 ``require_models``）。
    """
    import tempfile
    import traceback

    from PIL import Image

    from .. import upscale

    lines: list[str] = [f"ImageForge {__version__} self-test"]
    ok = True
    try:
        root = upscale.weights_root()
        packs = {key: upscale.pack_ready(key) for key in ("ncnn", "apisr")}
        lines.append(f"models: root={root}")
        lines.append("packs: " + ", ".join(f"{key}={value}" for key, value in packs.items()))
        try:
            from ..upscale import onnx_runner

            lines.append("onnx: " + onnx_runner.describe_backend())
        except Exception as exc:  # noqa: BLE001 - 自检报告而不中断
            lines.append(f"onnx: 不可用（{exc}）")
        if not any(packs.values()):
            lines.append("提示: 未安装放大模型，可在界面「下载模型」，或运行 ImageForge.exe --install-models")
            if require_models:
                ok = False
            lines.append("result: " + ("FAILED" if not ok else "OK (未安装模型)"))
            _write_selftest(lines)
            print("\n".join(lines))
            return 0 if ok else 1
        if packs["ncnn"]:
            exe = upscale.ncnn_exe()
            lines.append(f"ncnn exe: {exe} exists={exe.exists()}")
            ok = ok and exe.exists()
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
            if packs["ncnn"]:
                model_id, scale, denoise = "cugan-se", 2, 0
            else:
                model_id, scale, denoise = "apisr-grl", 4, None
            lines.append(f"probe model: {model_id} scale={scale}")
            result = upscale.upscale_file(
                src,
                upscale.UpscaleOptions(model_id=model_id, device="auto", scale=scale, denoise=denoise),
                out_root=Path(tmp) / "out",
            )
            lines.append(
                f"upscale: ok={result.ok} size={result.width}x{result.height} "
                f"seconds={result.seconds:.1f} message={result.message}"
            )
            expected = 128 if model_id == "cugan-se" else 256
            ok = ok and result.ok and (result.width, result.height) == (expected, expected)
            if result.warnings:
                lines.append("warnings: " + " / ".join(result.warnings))
    except Exception:  # noqa: BLE001 - 自检要报告任何异常而不是弹窗
        lines.append(traceback.format_exc())
        ok = False
    lines.append("result: " + ("OK" if ok else "FAILED"))
    _write_selftest(lines)
    print("\n".join(lines))
    return 0 if ok else 1


def _write_selftest(lines: list[str]) -> None:
    import tempfile

    report = Path(tempfile.gettempdir()) / "ImageForge-selftest.txt"
    try:
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _run_install_models(pack: str = "all") -> int:
    """无界面下载放大模型（供脚本/首启配置使用）。"""
    from .. import upscale

    keys = ["ncnn", "apisr"] if pack in ("all", "") else [pack]
    keys = [key for key in keys if not upscale.pack_ready(key)]
    if not keys:
        print("模型已安装，无需下载")
        return 0
    ok = True
    for key in keys:
        label = upscale.PACK_BY_KEY[key].label
        print(f"下载 {label} …")
        try:
            upscale.download_pack(
                key,
                progress=lambda done, total, name=label: print(
                    f"\r{name} {done / 1048576:.1f}/{total / 1048576:.1f} MB", end="", flush=True
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 命令行要给出可读错误
            print(f"\n下载失败：{exc}")
            ok = False
            continue
        print(f"\n{label} 已安装到 {upscale.weights_root()}")
        ok = ok and upscale.pack_ready(key)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ImageForge", description="图片格式转换与多尺寸 ICO 生成")
    parser.add_argument("--theme", choices=["system", "light", "dark"], help="强制主题")
    parser.add_argument("--single", action="store_true", help="单实例（默认开启）")
    parser.add_argument("--check", action="store_true", help="自检超分权重并退出（不打开界面）")
    parser.add_argument(
        "--require-models",
        action="store_true",
        help="配合 --check：模型未安装时视为失败",
    )
    parser.add_argument(
        "--install-models",
        nargs="?",
        const="all",
        choices=["all", "ncnn", "apisr"],
        help="下载放大模型后退出（ncnn 约 44 MB，apisr 约 59 MB；默认 all）",
    )
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.install_models:
        return _run_install_models(args.install_models)
    if args.check:
        return _run_selftest(require_models=args.require_models)

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
