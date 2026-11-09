"""色板体检：按 WCAG 2.1 校验关键前景/背景组合。

用法：python tools/check_contrast.py [--min-text 4.5]
退出码 0 表示全部达标。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imgforge.theme import PALETTES, contrast_ratio  # noqa: E402

# (前景 token, 背景 token, 说明, 最低要求)
TEXT_PAIRS = [
    ("text_primary", "surface", "正文 / 窗口背景", 7.0),
    ("text_primary", "surface_raised", "正文 / 卡片", 7.0),
    ("text_secondary", "surface", "说明文字 / 窗口背景", 4.5),
    ("text_secondary", "surface_raised", "说明文字 / 卡片", 4.5),
    ("text_primary", "surface_sunken", "正文 / 凹陷面", 4.5),
    ("on_accent", "accent", "主按钮文字 / 主色", 4.5),
    ("on_accent", "accent_hover", "主按钮文字 / 悬停", 4.5),
    ("on_accent", "accent_active", "主按钮文字 / 按下", 4.5),
    ("accent_text", "surface_raised", "链接文字 / 卡片", 4.5),
    ("text_primary", "accent_subtle", "正文 / 选中底色", 4.5),
    ("danger", "danger_subtle", "错误 / 错误底", 4.5),
    ("success", "success_subtle", "成功 / 成功底", 4.5),
    ("warning", "warning_subtle", "警告 / 警告底", 4.5),
]

UI_PAIRS = [
    ("focus", "surface_raised", "焦点环 / 卡片", 3.0),
    ("focus", "surface", "焦点环 / 窗口背景", 3.0),
    ("accent", "surface_raised", "主色块 / 卡片", 3.0),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-text", type=float, default=4.5)
    args = parser.parse_args()

    failures = 0
    for mode, palette in PALETTES.items():
        print(f"\n=== {mode} ===")
        for fg, bg, label, minimum in TEXT_PAIRS + UI_PAIRS:
            if mode == "dark" and (fg, bg, label, minimum) in []:
                continue
            ratio = contrast_ratio(palette[fg], palette[bg])
            need = max(minimum, args.min_text) if (fg, bg, label, minimum) in TEXT_PAIRS else minimum
            ok = ratio >= need
            failures += 0 if ok else 1
            print(f"[{'PASS' if ok else 'FAIL'}] {label:<22} {palette[fg]} on {palette[bg]}  {ratio:5.2f}:1  (>= {need})")
    print()
    print("全部达标" if failures == 0 else f"{failures} 项未达标")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())