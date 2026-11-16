"""设计 token：颜色 / 间距 / 圆角 / 字号 / 动效。

三层架构中的 ①原始层 + ②语义层。UI 代码只允许引用语义角色
（`theme.color("text_secondary")`），不允许出现字面量色值。
"""

from __future__ import annotations

from typing import Dict

# --------------------------------------------------------------- 语义色彩 --
# 角色命名按用途，不按外观；明暗两套一一对应，缺一个就会 KeyError。
LIGHT: Dict[str, str] = {
    "window": "#F2F4F7",          # 应用底色（最远）
    "surface": "#FFFFFF",          # 卡片 / 面板
    "surface_raised": "#FFFFFF",   # 浮起表面（对话框）
    "surface_sunken": "#E9EDF2",   # 输入框、缩略图底
    "surface_hover": "#F3F6FA",    # 行悬停
    "border": "#E2E6EC",
    "border_strong": "#CBD2DB",
    "text_primary": "#14171D",
    "text_secondary": "#5B6572",
    "text_disabled": "#9AA4B2",
    "accent": "#2B63D9",
    "accent_hover": "#2153BE",
    "accent_active": "#1A459E",
    "accent_subtle": "#E9F0FE",
    "accent_subtle_hover": "#DBE7FD",
    "accent_text": "#1F4FBF",      # 淡底上的强调文字
    "on_accent": "#FFFFFF",
    "focus": "#2B63D9",
    "danger": "#C8303A",
    "danger_subtle": "#FDECED",
    "success": "#157F4C",
    "success_subtle": "#E7F6EE",
    "warning": "#946200",
    "warning_subtle": "#FDF3DC",
    "checker_a": "#FFFFFF",
    "checker_b": "#E8EBF0",
    "shadow": "#1B2430",           # 仅用于阴影 alpha
}

DARK: Dict[str, str] = {
    "window": "#111317",
    "surface": "#191C22",
    "surface_raised": "#1F232A",
    "surface_sunken": "#0E1014",
    "surface_hover": "#232830",
    "border": "#2B313B",
    "border_strong": "#3B424E",
    "text_primary": "#E9ECF2",
    "text_secondary": "#A1AAB7",
    "text_disabled": "#6A7382",
    "accent": "#6EA8FE",
    "accent_hover": "#8CBCFF",
    "accent_active": "#5B96F2",
    "accent_subtle": "#1B2740",
    "accent_subtle_hover": "#21304E",
    "accent_text": "#A8C8FF",
    "on_accent": "#081120",
    "focus": "#7FB2FF",
    "danger": "#F1727C",
    "danger_subtle": "#3A1D21",
    "success": "#4CC38A",
    "success_subtle": "#16301F",
    "warning": "#E3B341",
    "warning_subtle": "#33280F",
    "checker_a": "#20242B",
    "checker_b": "#171A20",
    "shadow": "#000000",
}

PALETTES = {"light": LIGHT, "dark": DARK}

# ------------------------------------------------------------ 间距与圆角 --
# 4 的倍数，7 档；不允许出现 5 / 7 / 13 这类手感的数。
SPACE: Dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "2xl": 32,
    "3xl": 48,
}

RADIUS: Dict[str, int] = {
    "sm": 6,     # 小控件（胶囊、输入框）
    "md": 10,    # 按钮、行
    "lg": 14,    # 卡片
    "xl": 20,    # 对话框
    "pill": 999,
}

# --------------------------------------------------------------- 字体阶梯 --
# 单位：逻辑像素（Qt6 会按 DPI 自动换算到物理像素）
FONTS: Dict[str, tuple[int, int]] = {
    # name: (像素字号, 字重)
    "display": (26, 600),
    "h1": (20, 600),
    "h2": (16, 600),
    "body": (14, 400),
    "body_medium": (14, 500),
    "body_bold": (14, 600),
    "caption": (12, 400),
    "caption_medium": (12, 500),
    "mono": (12, 400),
}

# --------------------------------------------------------------- 动效 token --
DURATIONS: Dict[str, int] = {
    "instant": 100,   # 悬停 / 按压
    "fast": 160,      # 小元素出现
    "base": 220,      # 面板展开
    "slow": 320,      # 大表面
}

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FAMILY_FALLBACK = "Segoe UI"
MONO_FAMILY = "Cascadia Mono"
