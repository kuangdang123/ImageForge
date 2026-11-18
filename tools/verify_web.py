"""ImageForge Web · 用 Pillow 校验离线编码产物（tools/web_test.js 生成）。

用法：python tools/verify_web.py [tmp/web]
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tmp" / "web"

failures: list[str] = []
checks: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        checks.append(name)
        print(f"[PASS] {name}")
    else:
        failures.append(name)
        print(f"[FAIL] {name} {detail}")


def center_pixel(image: Image.Image) -> tuple[int, ...]:
    return image.convert("RGBA").getpixel((image.width // 2, image.height // 2))


def main() -> int:
    report_path = out_dir / "report.json"
    if not report_path.exists():
        print(f"找不到 {report_path}，先运行 node tools/web_test.js")
        return 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"产物目录：{report['outDir']}")
    print(f"JS 侧检查：{len(report['checks'])} 项\n")

    ico_dir = out_dir

    # --- ICO（BMP 载荷）---
    with Image.open(ico_dir / "test_bmp.ico") as image:
        sizes = sorted(image.info.get("sizes", set()))
        check("Pillow 可打开 ICO(BMP)", True)
        check("ICO(BMP) 尺寸档位", sizes == [(16, 16), (32, 32), (48, 48), (256, 256)], str(sizes))
        image.size = (256, 256)
        image.load()
        center = center_pixel(image)
        check("ICO(BMP) 中心是蓝色方块", abs(center[0] - 20) < 12 and abs(center[2] - 220) < 12, str(center))
        corner = image.convert("RGBA").getpixel((1, 1))
        check("ICO(BMP) 四角透明", corner[3] == 0, str(corner))

    # --- ICO（PNG 载荷）---
    with Image.open(ico_dir / "test_png.ico") as image:
        sizes = sorted(image.info.get("sizes", set()))
        check("Pillow 可打开 ICO(PNG)", True)
        check("ICO(PNG) 尺寸档位", sizes == [(16, 16), (32, 32), (48, 48), (256, 256)], str(sizes))
        image.size = (256, 256)
        image.load()
        center = center_pixel(image)
        check("ICO(PNG) 中心是蓝色方块", abs(center[0] - 20) < 12 and abs(center[2] - 220) < 12, str(center))

    # --- 16 位小图可读 ---
    with Image.open(ico_dir / "test_bmp.ico") as image:
        image.size = (16, 16)
        image.load()
        check("ICO 16px 可解码", image.width == 16 and image.height == 16)

    # --- BMP 24 位 ---
    with Image.open(out_dir / "test_24.bmp") as image:
        check("BMP 尺寸 64×64", image.size == (64, 64), str(image.size))
        check("BMP 模式 RGB", image.mode == "RGB", image.mode)
        # 自底向上写入，校验中心与角落颜色
        center = image.getpixel((32, 32))
        check("BMP 中心蓝色", abs(center[0] - 20) < 12 and abs(center[2] - 220) < 12, str(center))
        # 24 位 BMP 没有 alpha，透明像素被写成黑色（应用层会先铺背景色再编码）
        corner = image.getpixel((1, 1))
        check("BMP 透明角落为黑", corner == (0, 0, 0), str(corner))
        inside = image.getpixel((10, 10))
        check("BMP 红色区域", abs(inside[0] - 220) < 12, str(inside))

    # --- TIFF ---
    with Image.open(out_dir / "test.tiff") as image:
        check("TIFF 尺寸 64×64", image.size == (64, 64), str(image.size))
        check("TIFF 模式 RGBA", image.mode == "RGBA", image.mode)
        center = image.getpixel((32, 32))
        check("TIFF 中心蓝色", abs(center[0] - 20) < 12 and abs(center[2] - 220) < 12, str(center))
        corner = image.getpixel((1, 1))
        check("TIFF 角落透明", corner[3] == 0, str(corner))

    # --- ZIP ---
    with zipfile.ZipFile(out_dir / "test.zip") as archive:
        names = archive.namelist()
        check("ZIP 文件数", len(names) == 2, str(names))
        check("ZIP 中文名可读", "中文.txt" in names, str(names))
        check("ZIP 内容正确", archive.read("a.txt") == b"hello")
        check("ZIP CRC 正确", archive.testzip() is None)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        return 1
    print(f"全部 {len(checks)} 项 Pillow 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
