"""从 assets/logo.png 生成多尺寸 app.ico（16–256，PNG 载荷）。

源图是用户提供的 256×256 插画，LOGO 直接复用同一张，
这样窗口图标、任务栏图标和标题栏 logo 是同一套视觉。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from imgforge import ico  # noqa: E402

ASSETS = ROOT / "imgforge" / "assets"


def main() -> int:
    source = ASSETS / "logo.png"
    if not source.exists():
        print(f"缺少源图：{source}")
        return 1
    with Image.open(source) as img:
        art = img.convert("RGBA")
    print(f"源图 {source.name}: {art.width}×{art.height}")

    sizes = ico.save_ico(art, ASSETS / "app.ico", sizes=ico.DEFAULT_SIZES, fit="contain", payload="png")
    print(f"app.ico: {sizes}")

    entries = ico.read_entries(ASSETS / "app.ico")
    for entry in entries:
        width = entry["size"][0]
        print(f"  {width:>3}px  {entry['format']:<3}  {entry['bytes']:>6} B  bit={entry['bit_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())