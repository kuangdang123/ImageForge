"""主题与设计 token。

分层：语义 token（palette）→ ttk 样式 / 自绘控件。
组件里不写死颜色、间距、字号；需要时一律从 Theme 取。

提供：DPI 感知、字体选择、ttk clam 主题定制、明暗两套色板。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict

# ---------------------------------------------------------------- 语义色板 --
# 只用语义命名（按用途），不按外观命名，方便整体换肤。

PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "surface": "#F4F5F7",
        "surface_raised": "#FFFFFF",
        "surface_sunken": "#ECEEF2",
        "surface_hover": "#EDF0F4",
        "border": "#E0E3E9",
        "border_strong": "#C3C9D2",
        "text_primary": "#1A1D23",
        "text_secondary": "#5B6472",
        "text_disabled": "#9AA3B0",
        "accent": "#0F6CBD",
        "accent_hover": "#0D5CA6",
        "accent_active": "#0B4D8A",
        "accent_text": "#0F6CBD",
        "on_accent": "#FFFFFF",
        "accent_subtle": "#E6F0FA",
        "accent_subtle_hover": "#D3E5F7",
        "focus": "#0F6CBD",
        "danger": "#B42318",
        "danger_subtle": "#FDECEA",
        "success": "#0F6B0F",
        "success_subtle": "#E7F4E7",
        "warning": "#8A5300",
        "warning_subtle": "#FFF3E0",
        "checker_a": "#E9ECF1",
        "checker_b": "#F8F9FB",
    },
    "dark": {
        "surface": "#191B1F",
        "surface_raised": "#212429",
        "surface_sunken": "#131518",
        "surface_hover": "#2A2E35",
        "border": "#32373F",
        "border_strong": "#4A515C",
        "text_primary": "#E8EBF0",
        "text_secondary": "#A3ACB9",
        "text_disabled": "#6E7783",
        "accent": "#2670B8",
        "accent_hover": "#2B77C0",
        "accent_active": "#1E5E9C",
        "accent_text": "#6FB4F2",
        "on_accent": "#FFFFFF",
        "accent_subtle": "#1B2C3D",
        "accent_subtle_hover": "#24384C",
        "focus": "#6FB4F2",
        "danger": "#F27B6D",
        "danger_subtle": "#3A1E1C",
        "success": "#6BCB6B",
        "success_subtle": "#17301A",
        "warning": "#E5B567",
        "warning_subtle": "#332611",
        "checker_a": "#2A2E35",
        "checker_b": "#22252A",
    },
}

# 间距阶梯（逻辑像素，4 的倍数）
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

# 字号阶梯（pt，随 DPI 缩放）
FONT_SIZES = {"display": 18, "h1": 15, "h2": 12, "body": 10, "caption": 9}

UI_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "Noto Sans CJK SC",
    "Arial",
)
MONO_FONT_CANDIDATES = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")


def enable_dpi_awareness() -> None:
    """在创建 Tk 之前调用，让窗口按物理像素绘制，避免系统位图拉伸发虚。"""
    if sys.platform != "win32":
        return
    try:  # Windows 8.1+：per-monitor v2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class Theme:
    """一个窗口一份：负责缩放比、字体、ttk 样式。"""

    def __init__(self, root: tk.Misc, mode: str = "light") -> None:
        self.root = root
        self.mode = mode if mode in PALETTES else "light"
        self.palette: Dict[str, str] = dict(PALETTES[self.mode])

        dpi = float(root.winfo_fpixels("1i"))
        self.scale = max(1.0, dpi / 96.0)
        # 让 Tk 以真实 DPI 解释 pt 字号
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass

        self.ui_family, self.mono_family = self._pick_families()
        self.fonts = self._build_fonts()
        self.style = ttk.Style(root)
        self.configure_ttk()

    # ------------------------------------------------------------- helpers --
    def px(self, n: float) -> int:
        """逻辑像素 → 物理像素。"""
        return int(round(n * self.scale))

    def sp(self, name: str) -> int:
        return self.px(SPACING[name])

    def color(self, role: str) -> str:
        try:
            return self.palette[role]
        except KeyError as exc:  # 拼错 token 时立刻暴露，而不是静默变黑
            raise KeyError(f"未知颜色 token: {role}") from exc

    def font(self, name: str) -> tkfont.Font:
        return self.fonts[name]

    # -------------------------------------------------------------- fonts --
    def _pick_families(self) -> tuple[str, str]:
        try:
            families = set(tkfont.families(self.root))
        except tk.TclError:
            families = set()
        ui = next((f for f in UI_FONT_CANDIDATES if f in families), "TkDefaultFont")
        mono = next((f for f in MONO_FONT_CANDIDATES if f in families), "TkFixedFont")
        return ui, mono

    def _build_fonts(self) -> Dict[str, tkfont.Font]:
        ui = self.ui_family
        return {
            "display": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["display"], weight="bold"),
            "h1": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["h1"], weight="bold"),
            "h2": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["h2"], weight="bold"),
            "body": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["body"]),
            "body_bold": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["body"], weight="bold"),
            "caption": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["caption"]),
            "caption_bold": tkfont.Font(root=self.root, family=ui, size=FONT_SIZES["caption"], weight="bold"),
            "mono": tkfont.Font(root=self.root, family=self.mono_family, size=FONT_SIZES["caption"]),
        }

    # ---------------------------------------------------------------- ttk --
    def configure_ttk(self) -> None:
        style = self.style
        p = self.palette
        f = self.fonts
        px = self.px

        style.theme_use("clam")
        style.configure(
            ".",
            background=p["surface"],
            foreground=p["text_primary"],
            font=f["body"],
            borderwidth=0,
            focuscolor=p["focus"],
            troughcolor=p["surface_sunken"],
            selectbackground=p["accent_subtle"],
            selectforeground=p["text_primary"],
            fieldbackground=p["surface_raised"],
            insertcolor=p["text_primary"],
        )
        style.configure("TFrame", background=p["surface"])
        style.configure("Raised.TFrame", background=p["surface_raised"])
        style.configure("Sunken.TFrame", background=p["surface_sunken"])
        style.configure("TLabel", background=p["surface"], foreground=p["text_primary"], font=f["body"])
        style.configure("Raised.TLabel", background=p["surface_raised"])
        style.configure("Secondary.TLabel", background=p["surface"], foreground=p["text_secondary"], font=f["caption"])
        style.configure("Secondary.Raised.TLabel", background=p["surface_raised"], foreground=p["text_secondary"], font=f["caption"])
        style.configure("H1.TLabel", background=p["surface"], foreground=p["text_primary"], font=f["h1"])
        style.configure("H2.TLabel", background=p["surface"], foreground=p["text_primary"], font=f["h2"])
        style.configure("Error.TLabel", background=p["surface_raised"], foreground=p["danger"], font=f["caption"])
        style.configure("Success.TLabel", background=p["surface_raised"], foreground=p["success"], font=f["caption"])

        # 普通按钮：次要动作
        style.configure(
            "TButton",
            background=p["surface_raised"],
            foreground=p["text_primary"],
            bordercolor=p["border_strong"],
            lightcolor=p["surface_raised"],
            darkcolor=p["surface_raised"],
            focusthickness=px(1),
            focuscolor=p["focus"],
            padding=(px(12), px(6)),
            relief="flat",
            font=f["body"],
        )
        style.map(
            "TButton",
            background=[("disabled", p["surface_sunken"]), ("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
            foreground=[("disabled", p["text_disabled"])],
            bordercolor=[("disabled", p["border"]), ("focus", p["focus"]), ("active", p["border_strong"])],
            lightcolor=[("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
            darkcolor=[("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
        )

        # 主要按钮
        style.configure(
            "Primary.TButton",
            background=p["accent"],
            foreground=p["on_accent"],
            bordercolor=p["accent"],
            lightcolor=p["accent"],
            darkcolor=p["accent"],
            focusthickness=px(2),
            focuscolor=p["on_accent"],
            padding=(px(18), px(8)),
            relief="flat",
            font=f["body_bold"],
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", p["surface_sunken"]), ("pressed", p["accent_active"]), ("active", p["accent_hover"])],
            foreground=[("disabled", p["text_disabled"])],
            bordercolor=[("disabled", p["border"]), ("pressed", p["accent_active"]), ("active", p["accent_hover"])],
            lightcolor=[("pressed", p["accent_active"]), ("active", p["accent_hover"])],
            darkcolor=[("pressed", p["accent_active"]), ("active", p["accent_hover"])],
        )

        # 工具栏幽灵按钮
        style.configure(
            "Ghost.TButton",
            background=p["surface"],
            foreground=p["text_primary"],
            bordercolor=p["surface"],
            lightcolor=p["surface"],
            darkcolor=p["surface"],
            focusthickness=px(1),
            padding=(px(8), px(5)),
            relief="flat",
            font=f["body"],
        )
        style.map(
            "Ghost.TButton",
            background=[("disabled", p["surface"]), ("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
            foreground=[("disabled", p["text_disabled"])],
            bordercolor=[("focus", p["focus"]), ("active", p["surface_hover"])],
            lightcolor=[("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
            darkcolor=[("pressed", p["surface_sunken"]), ("active", p["surface_hover"])],
        )

        # 队列
        style.configure(
            "Queue.Treeview",
            background=p["surface_raised"],
            fieldbackground=p["surface_raised"],
            foreground=p["text_primary"],
            rowheight=px(42),
            borderwidth=0,
            relief="flat",
            font=f["body"],
        )
        style.map(
            "Queue.Treeview",
            background=[("selected", p["accent_subtle"])],
            foreground=[("selected", p["text_primary"])],
        )
        style.configure(
            "Queue.Treeview.Heading",
            background=p["surface_raised"],
            foreground=p["text_secondary"],
            relief="flat",
            padding=(px(8), px(6)),
            font=f["caption_bold"],
            bordercolor=p["border"],
        )
        style.map("Queue.Treeview.Heading", background=[("active", p["surface_hover"])])

        # 细滚动条
        style.configure(
            "Thin.Vertical.TScrollbar",
            background=p["border_strong"],
            troughcolor=p["surface_raised"],
            bordercolor=p["surface_raised"],
            arrowcolor=p["text_secondary"],
            width=px(10),
            arrowsize=px(10),
        )
        style.map(
            "Thin.Vertical.TScrollbar",
            background=[("pressed", p["text_secondary"]), ("active", p["border_strong"])],
        )

        # 进度条
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=p["surface_sunken"],
            background=p["accent"],
            bordercolor=p["surface_sunken"],
            lightcolor=p["accent"],
            darkcolor=p["accent"],
            thickness=px(4),
        )

        # 输入框（备用）
        style.configure(
            "TEntry",
            fieldbackground=p["surface_raised"],
            foreground=p["text_primary"],
            bordercolor=p["border_strong"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            insertcolor=p["text_primary"],
            padding=(px(6), px(4)),
        )


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 2.1 相对对比度，供 tools/check_contrast.py 体检用。"""

    def _lum(value: str) -> float:
        value = value.lstrip("#")
        rgb = [int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    la, lb = _lum(hex_a), _lum(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)