"""用 PyInstaller 打单文件 exe（发布用）。

    python tools/build_exe.py --qt          # 2.0：PySide6 界面（默认）
    python tools/build_exe.py --legacy-tk   # 1.0：tkinter 界面
    python tools/build_exe.py --onedir      # 文件夹版本，启动更快

默认输出 dist/ImageForge.exe（单文件，不含 Python 环境）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imgforge.version import __version__  # noqa: E402

QT_VERSION = __version__
TK_VERSION = "1.0.0"

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(MAJOR, MINOR, PATCH, 0),
    prodvers=(MAJOR, MINOR, PATCH, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404B0',
          [
            StringStruct('CompanyName', 'ImageForge'),
            StringStruct('FileDescription', 'DESCRIPTION'),
            StringStruct('FileVersion', 'VERSION'),
            StringStruct('InternalName', 'ImageForge'),
            StringStruct('LegalCopyright', ''),
            StringStruct('OriginalFilename', 'ImageForge.exe'),
            StringStruct('ProductName', 'ImageForge'),
            StringStruct('ProductVersion', 'VERSION'),
            StringStruct('Comments', '图片格式转换与多尺寸 ICO 生成'),
          ],
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [2052, 1200])]),
  ]
)
"""

COMMON_EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib", "IPython", "pytest",
    "PyQt5", "PyQt6", "PySide2", "torch", "cv2", "pydantic",
]

QT_EXCLUDES = [
    "onnxruntime.transformers", "onnxruntime.quantization", "onnxruntime.tools",
    "onnxruntime.test", "onnxruntime.training",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtSerialPort", "PySide6.QtSensors",
    "PySide6.QtPositioning", "PySide6.QtNfc", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtHelp", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "tkinter", "_tkinter", "tkinterdnd2",
]


def version_info(version: str, description: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return (
        VERSION_TEMPLATE.replace("MAJOR", str(major))
        .replace("MINOR", str(minor))
        .replace("PATCH", str(patch))
        .replace("VERSION", f"{version}.0")
        .replace("DESCRIPTION", description)
    )


def main() -> int:
    onedir = "--onedir" in sys.argv
    legacy = "--legacy-tk" in sys.argv
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    if legacy:
        version, description, entry = TK_VERSION, "ImageForge 图片格式转换", "entry_gui.py"
    else:
        version, description, entry = QT_VERSION, "ImageForge 2 图片格式转换", "entry_qt.py"

    version_file = BUILD / "version_info.txt"
    version_file.write_text(version_info(version, description), encoding="utf-8")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        "--windowed",
        "--name", "ImageForge",
        "--icon", str(ROOT / "imgforge" / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'imgforge' / 'assets'}{';'}imgforge/assets",
        "--version-file", str(version_file),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
    ]
    excludes = list(COMMON_EXCLUDES) + ([] if legacy else QT_EXCLUDES)
    if not legacy:
        # ONNX Runtime 依赖 numpy（APISR 推理要用），Qt 版不能排除。
        excludes = [name for name in excludes if name != "numpy"]
    if legacy:
        args.extend(["--collect-all", "tkinterdnd2"])
    else:
        # APISR 走 ONNX Runtime（DirectML）推理；onnx_runner 里是延迟导入，
        # PyInstaller 静态分析看不到，这里显式收集运行库与官方 provider DLL。
        args.extend(
            [
                "--hidden-import",
                "onnxruntime",
                "--hidden-import",
                "onnxruntime.capi._pybind_state",
                "--collect-binaries",
                "onnxruntime",
                "--collect-submodules",
                "onnxruntime.capi",
            ]
        )
        # 模型权重不打包：2.3.0 起 Real-CUGAN 改为按需下载（约 44 MB），
        # 2.4.0 起 APISR ONNX 同样按需下载（约 59 MB）。
        if (ROOT / "dist" / "models" / "realcugan-ncnn-models.zip").is_file():
            print("提示：模型包将在 release 资产中单独上传，不打进 exe")
    for name in excludes:
        args.extend(["--exclude-module", name])
    args.append(str(ROOT / "tools" / entry))

    print("PyInstaller:", " ".join(args[2:]))
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败", file=sys.stderr)
        return result.returncode
    exe = DIST / ("ImageForge/ImageForge.exe" if onedir else "ImageForge.exe")
    if exe.exists():
        print(f"完成：{exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
