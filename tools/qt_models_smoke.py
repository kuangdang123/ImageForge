"""离屏验证「放大模型未安装 → 安装卡 → 按模型包安装 → 门禁恢复」的界面流程。

用法：python tools/qt_models_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NCNN_ARCHIVE = ROOT / "dist" / "models" / "realcugan-ncnn-models.zip"
APISR_ARCHIVE = ROOT / "dist" / "models" / "apisr-onnx-models.zip"
MODELS = ROOT / "tmp" / "models_smoke"


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from imgforge import upscale
    from imgforge.qt.app import build_app
    from imgforge.qt.mainwindow import MainWindow
    from imgforge.qt.theme import Theme
    from imgforge.settings import Settings

    for archive in (NCNN_ARCHIVE, APISR_ARCHIVE):
        if not archive.is_file():
            print(f"缺少模型包：{archive}（先运行 tools/pack_models.py）")
            return 1

    shutil.rmtree(MODELS, ignore_errors=True)
    MODELS.mkdir(parents=True)
    os.environ["IMAGEFORGE_MODELS_DIR"] = str(MODELS)
    os.environ.setdefault("IMAGEFORGE_HOME", str(ROOT / "tmp" / "settings_models"))
    (ROOT / "tmp" / "settings_models").mkdir(parents=True, exist_ok=True)
    (ROOT / "tmp" / "settings_models" / "settings.json").unlink(missing_ok=True)

    app: QApplication = build_app([sys.argv[0], "-platform", "offscreen"])
    settings = Settings.load()
    theme = Theme(mode="light")
    theme.apply()
    window = MainWindow(theme, settings)
    window.resize(1180, 800)
    window.show()
    app.processEvents()

    panel = window.upscale_panel
    window.nav.set_value("upscale", emit=True)
    app.processEvents()

    checks: list[tuple[str, bool]] = []
    checks.append(("未安装时 is_ready=False", panel.is_ready() is False))
    checks.append(("显示安装卡", panel.install_card.isVisible()))
    checks.append(("安装卡有两行", len(panel._install_buttons) == 2))
    checks.append(("隐藏模型卡", not panel.model_card.isVisible()))
    checks.append(("隐藏输出卡", not panel.output_card.isVisible()))
    checks.append(("开始按钮禁用", not window.start_btn.isEnabled()))

    installed = upscale.install_archive(NCNN_ARCHIVE, MODELS)
    panel.refresh()
    app.processEvents()
    checks.append(("安装 ncnn 返回 ['ncnn']", installed == ["ncnn"]))
    checks.append(("ncnn 安装后 is_ready=True", panel.is_ready() is True))
    checks.append(("模型卡显示", panel.model_card.isVisible()))
    checks.append(("模型下拉 6 项", len(panel.model_box.options()) == 6))
    pills = [panel.model_box.option(key)["pills"] for key in ("cugan-pro", "apisr-grl", "apisr-dat")]
    checks.append(("每个模型都有彩色标签", all(bool(item) for item in pills)))

    panel.model_box.set_value("apisr-grl", emit=True)
    app.processEvents()
    checks.append(("选 APISR 但未装包 → selected_ready=False", panel.selected_ready() is False))
    checks.append(("未装包时开始按钮禁用", not window.start_btn.isEnabled()))
    checks.append(("提示文案含「下载」", "下载" in panel.model_note.text()))

    installed = upscale.install_archive(APISR_ARCHIVE, MODELS)
    panel.refresh()
    app.processEvents()
    checks.append(("安装 apisr 返回 ['apisr']", installed == ["apisr"]))
    checks.append(("APISR 就绪", panel.selected_ready() is True))
    checks.append(("两包都安装", sorted(upscale.installed_packs()) == ["apisr", "ncnn"]))

    ok = True
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print("\nresult:", "PASS" if ok else "FAIL")
    window.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
