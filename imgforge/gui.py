"""ImageForge 主界面。

结构：标题栏 → 工具栏 → 左侧队列 / 右侧设置 → 底部状态与主操作。
所有颜色、字号、间距都来自 theme，界面代码里不写死色值。
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Iterable, Optional

from PIL import Image, ImageGrab, ImageTk

from . import converter, ico, single_instance
from .converter import ConvertOptions, ConvertResult, ImageInfo
from .picker import FolderPicker
from .settings import Settings
from .theme import Theme, enable_dpi_awareness
from .widgets import ChipGroup, CheckRow, DropZone, IconButton, QueueRow, ScrollFrame, TextButton, Tooltip, truncate

ASSETS = Path(__file__).resolve().parent / "assets"

try:  # pragma: no cover - 环境相关
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
    _BaseTk = TkinterDnD.Tk
except Exception:  # noqa: BLE001 - 没装也能用，只是少了拖拽
    DND_AVAILABLE = False
    DND_FILES = None  # type: ignore[assignment]
    _BaseTk = tk.Tk


FILE_TYPES = [
    ("图片文件", " ".join(f"*{ext}" for ext in sorted(converter.INPUT_EXTS))),
    ("所有文件", "*.*"),
]


def open_folder(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def reveal_file(path: Path) -> None:
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
    except OSError:
        open_folder(path.parent)


def apply_title_bar_theme(window: tk.Misc, dark: bool) -> None:
    """让 Windows 标题栏跟随应用主题；失败时保持系统默认。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（新/旧编号）
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:  # noqa: BLE001 - 非 Win11 或 API 缺失时忽略
        pass


def set_app_user_model_id() -> None:
    """让任务栏把窗口当成独立应用，显示应用自己的图标而不是 python 的。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ImageForge.Desktop.1")
    except Exception:  # noqa: BLE001 - 老系统缺少该 API 时忽略
        pass


@dataclass
class QueueItem:
    path: Path
    row: QueueRow
    size: int = 0
    info: Optional[ImageInfo] = None
    status: str = "reading"
    thumb: Optional[Image.Image] = None
    result: Optional[ConvertResult] = None
    message: str = ""


class App(_BaseTk):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.restart_theme = False
        self.theme = Theme(self, str(settings["theme"]))

        self.items: list[QueueItem] = []
        self.by_path: dict[str, QueueItem] = {}
        self.selected: Optional[QueueItem] = None
        self._photos: list[ImageTk.PhotoImage] = []
        self._probe_queue: "queue.Queue[tuple]" = queue.Queue()
        self._probe_thread: Optional[threading.Thread] = None
        self._probe_pending: list[QueueItem] = []
        self._convert_queue: "queue.Queue[tuple]" = queue.Queue()
        self._convert_thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._converting = False
        self._done = 0
        self._failed = 0
        self._last_out_dir: Optional[Path] = None
        self._total = 0
        self._pending_autostart = False
        self._picker: Optional["FolderPicker"] = None

        self._build_window()
        self._bind_keys()
        self._register_dnd()
        self._update_summary()
        self._set_status("就绪 · 把图片或文件夹拖进窗口，或点击「添加图片」")
        # 非客户区要等窗口真正映射之后才能改，太早调用会静默失效
        self.after(150, self._apply_titlebar)
        self.after(600, self._apply_titlebar)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================ 窗口 --
    def _apply_titlebar(self) -> None:
        apply_title_bar_theme(self, self.theme.mode == "dark")

    def _build_window(self) -> None:
        theme = self.theme
        self.title("ImageForge · 图片格式转换")
        geometry = str(self.settings["window"] or "1120x680")
        try:
            width_text, height_text = geometry.lower().split("x")
            width, height = theme.px(float(width_text)), theme.px(float(height_text))
        except (ValueError, AttributeError):
            width, height = theme.px(1120), theme.px(680)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(width, screen_w - theme.px(48))
        height = min(height, screen_h - theme.px(96))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - theme.px(10))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(theme.px(960), theme.px(560))
        self.configure(bg=theme.color("surface"))
        try:
            self.iconbitmap(default=str(ASSETS / "app.ico"))
        except tk.TclError:
            pass

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._build_footer()

    # -- 标题栏 ----------------------------------------------------------
    def _build_header(self) -> None:
        theme = self.theme
        header = tk.Frame(self, bg=theme.color("surface"))
        header.grid(row=0, column=0, sticky="ew", padx=theme.sp("xl"), pady=(theme.sp("md"), 0))
        header.columnconfigure(2, weight=1)

        try:
            logo = Image.open(ASSETS / "logo.png").convert("RGBA")
            side = theme.px(32)
            logo = logo.resize((side, side), Image.Resampling.LANCZOS)
            self._logo_photo = ImageTk.PhotoImage(logo, master=self)
            tk.Label(header, image=self._logo_photo, bg=theme.color("surface"), bd=0).grid(row=0, column=0, rowspan=2, sticky="w")
        except OSError:
            self._logo_photo = None

        tk.Label(
            header, text="ImageForge", bg=theme.color("surface"), fg=theme.color("text_primary"),
            font=theme.font("display"), anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(theme.px(10), 0))
        tk.Label(
            header, text="图片格式转换 · 多尺寸 ICO 生成", bg=theme.color("surface"),
            fg=theme.color("text_secondary"), font=theme.font("caption"), anchor="w",
        ).grid(row=1, column=1, sticky="w", padx=(theme.px(10), 0))

        glyph = "moon" if theme.mode == "light" else "sun"
        hint = "切换到深色模式" if theme.mode == "light" else "切换到浅色模式"
        self.theme_btn = IconButton(header, theme, glyph, self._toggle_theme, background="surface",
                                    size=theme.px(32), tooltip=hint)
        self.theme_btn.grid(row=0, column=3, rowspan=2, sticky="e")

    # -- 工具栏 ----------------------------------------------------------
    def _build_toolbar(self) -> None:
        theme = self.theme
        bar = tk.Frame(self, bg=theme.color("surface"))
        bar.grid(row=1, column=0, sticky="ew", padx=theme.sp("xl"), pady=(theme.sp("md"), theme.sp("sm")))

        self.add_files_btn = self._flat_button(bar, "添加图片", self._choose_files, primary=True)
        self.add_files_btn.pack(side="left")
        self.add_folder_btn = self._flat_button(bar, "选择文件夹…", self._choose_folder, primary=True)
        self.add_folder_btn.pack(side="left", padx=(theme.px(8), 0))
        self.paste_btn = self._flat_button(bar, "粘贴图片", self._paste_clipboard, primary=False)
        self.paste_btn.pack(side="left", padx=(theme.px(4), 0))

        self.clear_btn = self._flat_button(bar, "清空列表", self._clear_all, primary=False)
        self.clear_btn.pack(side="right")
        self.count_label = tk.Label(bar, text="", bg=theme.color("surface"),
                                    fg=theme.color("text_secondary"), font=theme.font("caption"), anchor="e")
        self.count_label.pack(side="right", padx=(0, theme.px(10)))

    def _flat_button(self, master: tk.Misc, text: str, command, *, primary: bool) -> tk.Button:
        """工具栏按钮：主按钮白底描边，次按钮纯文字。"""
        theme = self.theme
        if primary:
            button = tk.Button(
                master, text=text, command=command, font=theme.font("body"),
                bg=theme.color("surface_raised"), fg=theme.color("text_primary"),
                activebackground=theme.color("surface_hover"), activeforeground=theme.color("text_primary"),
                relief="flat", bd=0, padx=theme.px(13), pady=theme.px(6), cursor="hand2",
                highlightthickness=1, highlightbackground=theme.color("border_strong"),
                highlightcolor=theme.color("focus"),
            )
        else:
            button = tk.Button(
                master, text=text, command=command, font=theme.font("body"),
                bg=theme.color("surface"), fg=theme.color("text_secondary"),
                activebackground=theme.color("surface_hover"), activeforeground=theme.color("text_primary"),
                relief="flat", bd=0, padx=theme.px(10), pady=theme.px(6), cursor="hand2",
                highlightthickness=0,
            )
        self._attach_button_hover(button)
        return button

    def _attach_button_hover(self, button: tk.Button) -> None:
        """tk.Button 的悬停态只管文字不管底色，这里补底色反馈（不改基色则跳过）。"""
        hover = self.theme.color("surface_hover")
        base = str(button.cget("bg"))

        def on_enter(_event: tk.Event) -> None:
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover)

        def on_leave(_event: tk.Event) -> None:
            button.configure(bg=base)

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")

    # -- 主体 ------------------------------------------------------------
    def _build_body(self) -> None:
        theme = self.theme
        body = tk.Frame(self, bg=theme.color("surface"))
        body.grid(row=2, column=0, sticky="nsew", padx=theme.sp("xl"), pady=(0, theme.sp("sm")))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        card = tk.Frame(body, bg=theme.color("surface_raised"), highlightthickness=1,
                        highlightbackground=theme.color("border"))
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        card_header = tk.Frame(card, bg=theme.color("surface_raised"))
        card_header.grid(row=0, column=0, sticky="ew", padx=theme.sp("lg"), pady=(theme.sp("md"), theme.sp("sm")))
        card_header.columnconfigure(1, weight=1)
        tk.Label(card_header, text="待处理", bg=theme.color("surface_raised"), fg=theme.color("text_primary"),
                 font=theme.font("h2")).grid(row=0, column=0, sticky="w")
        self.summary_label = tk.Label(card_header, text="", bg=theme.color("surface_raised"),
                                      fg=theme.color("text_secondary"), font=theme.font("caption"), anchor="e")
        self.summary_label.grid(row=0, column=1, sticky="e")

        self.list_area = tk.Frame(card, bg=theme.color("surface_raised"))
        self.list_area.grid(row=1, column=0, sticky="nsew")
        self.list_area.columnconfigure(0, weight=1)
        self.list_area.rowconfigure(0, weight=1)

        self.scroll = ScrollFrame(self.list_area, theme, background="surface_raised")
        self.scroll.grid(row=0, column=0, sticky="nsew")

        self.empty_frame = tk.Frame(self.list_area, bg=theme.color("surface_raised"))
        self.empty_frame.grid(row=0, column=0, sticky="nsew")
        self.empty_frame.columnconfigure(0, weight=1)
        self.empty_frame.rowconfigure(0, weight=1)
        self.drop_zone = DropZone(
            self.empty_frame, theme,
            title="把图片拖到这里",
            subtitle="支持 PNG · JPG · WebP · GIF · BMP · TIFF · AVIF · ICO",
            hint="文件夹会递归扫描；也可以按 Ctrl+V 粘贴剪贴板图片",
        )
        self.drop_zone.grid(row=0, column=0, sticky="nsew", padx=theme.px(6), pady=theme.px(6))

        self._build_settings(body)

    def _build_settings(self, body: tk.Frame) -> None:
        theme = self.theme
        panel = tk.Frame(body, bg=theme.color("surface_raised"), highlightthickness=1,
                         highlightbackground=theme.color("border"), width=theme.px(360))
        panel.grid(row=0, column=1, sticky="ns", padx=(theme.sp("lg"), 0))
        panel.grid_propagate(False)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        header = tk.Frame(panel, bg=theme.color("surface_raised"))
        header.grid(row=0, column=0, sticky="ew", padx=theme.sp("lg"), pady=(theme.sp("md"), 0))
        tk.Label(header, text="输出设置", bg=theme.color("surface_raised"), fg=theme.color("text_primary"),
                 font=theme.font("h2")).pack(side="left")

        self.settings_scroll = ScrollFrame(panel, theme, background="surface_raised")
        self.settings_scroll.grid(row=1, column=0, sticky="nsew", padx=(theme.sp("lg"), theme.px(4)),
                                  pady=(theme.sp("sm"), theme.px(6)))
        box = self.settings_scroll.inner
        box.columnconfigure(0, weight=1)
        gap = theme.px(8)

        row = 0
        self._section_label(box, "输出格式").grid(row=row, column=0, sticky="ew")
        row += 1
        self.format_group = ChipGroup(
            box, theme,
            options=[(converter.FORMATS[key].label, key) for key in converter.FORMAT_ORDER],
            value=str(self.settings["target"]),
            command=self._on_format_change,
            columns=4,
        )
        self.format_group.grid(row=row, column=0, sticky="ew", pady=(theme.px(6), 0))
        row += 1
        self.format_note = tk.Label(box, text="", bg=theme.color("surface_raised"),
                                    fg=theme.color("text_secondary"), font=theme.font("caption"),
                                    anchor="w", justify="left")
        self.format_note.grid(row=row, column=0, sticky="ew", pady=(theme.px(5), gap))
        row += 1

        self.quality_frame = tk.Frame(box, bg=theme.color("surface_raised"))
        self.quality_frame.grid(row=row, column=0, sticky="ew", pady=(0, gap))
        self.quality_frame.columnconfigure(1, weight=1)
        tk.Label(self.quality_frame, text="质量", bg=theme.color("surface_raised"),
                 fg=theme.color("text_secondary"), font=theme.font("caption_bold")).grid(row=0, column=0, sticky="w")
        self.quality_value = tk.Label(self.quality_frame, text="92", bg=theme.color("surface_raised"),
                                      fg=theme.color("text_primary"), font=theme.font("mono"), width=3, anchor="e")
        self.quality_value.grid(row=0, column=2, sticky="e")
        self.quality_scale = tk.Scale(
            self.quality_frame, from_=10, to=100, orient="horizontal", showvalue=0,
            command=self._on_quality_move, bg=theme.color("surface_raised"),
            troughcolor=theme.color("surface_sunken"), activebackground=theme.color("accent_hover"),
            highlightthickness=0, bd=0, relief="flat", sliderlength=theme.px(16), sliderrelief="flat",
            width=theme.px(4), length=theme.px(160), fg=theme.color("accent"),
        )
        self.quality_scale.grid(row=0, column=1, sticky="ew", padx=(theme.px(8), theme.px(6)))
        row += 1

        self.ico_frame = tk.Frame(box, bg=theme.color("surface_raised"))
        self.ico_frame.grid(row=row, column=0, sticky="ew")
        self.ico_frame.columnconfigure(0, weight=1)
        row += 1
        self._build_ico_settings(self.ico_frame)

        self.misc_frame = tk.Frame(box, bg=theme.color("surface_raised"))
        self.misc_frame.grid(row=row, column=0, sticky="ew", pady=(gap, 0))
        self._build_misc_settings(self.misc_frame)
        row += 1
        box.rowconfigure(row, weight=1)

        self._sync_format_panels()

    def _section_label(self, master: tk.Misc, text: str) -> tk.Label:
        return tk.Label(master, text=text, bg=self.theme.color("surface_raised"),
                        fg=self.theme.color("text_secondary"), font=self.theme.font("caption_bold"), anchor="w")

    def _build_ico_settings(self, parent: tk.Frame) -> None:
        theme = self.theme
        parent.columnconfigure(0, weight=1)
        gap = theme.px(10)

        self._section_label(parent, "图标尺寸").grid(row=0, column=0, sticky="ew")
        sizes_grid = tk.Frame(parent, bg=theme.color("surface_raised"))
        sizes_grid.grid(row=1, column=0, sticky="ew", pady=(theme.px(6), gap))
        chosen = set(self.settings.sizes())
        self.size_checks: dict[int, CheckRow] = {}
        for index, size in enumerate(ico.DEFAULT_SIZES):
            check = CheckRow(
                sizes_grid, theme, str(size), size in chosen,
                command=lambda value, s=size: self._on_size_toggle(s, value),
                background="surface_raised", compact=True,
            )
            check.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, theme.px(8)), pady=theme.px(1))
            self.size_checks[size] = check

        fit_row = tk.Frame(parent, bg=theme.color("surface_raised"))
        fit_row.grid(row=2, column=0, sticky="ew", pady=(0, theme.px(8)))
        tk.Label(fit_row, text="非正方形", bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
                 font=theme.font("caption_bold")).pack(side="left")
        self.fit_group = ChipGroup(
            fit_row, theme, [("留白", "contain"), ("填充裁切", "fill"), ("拉伸", "stretch")],
            str(self.settings["ico_fit"]), self._on_fit_change, columns=3,
        )
        self.fit_group.pack(side="left", padx=(theme.px(10), 0))

        payload_row = tk.Frame(parent, bg=theme.color("surface_raised"))
        payload_row.grid(row=3, column=0, sticky="ew")
        tk.Label(payload_row, text="像素数据", bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
                 font=theme.font("caption_bold")).pack(side="left")
        self.payload_group = ChipGroup(
            payload_row, theme, [("PNG 压缩", "png"), ("BMP 兼容", "bmp")],
            str(self.settings["ico_payload"]), self._on_payload_change, columns=2,
        )
        self.payload_group.pack(side="left", padx=(theme.px(10), 0))

    def _build_misc_settings(self, parent: tk.Frame) -> None:
        theme = self.theme
        parent.columnconfigure(0, weight=1)
        self._section_label(parent, "其它").grid(row=0, column=0, sticky="ew")
        grid = tk.Frame(parent, bg=theme.color("surface_raised"))
        grid.grid(row=1, column=0, sticky="ew", pady=(theme.px(4), 0))
        self.metadata_check = CheckRow(
            grid, theme, "保留 EXIF / 色彩配置", bool(self.settings["keep_metadata"]),
            command=lambda value: self._save_setting("keep_metadata", value),
            background="surface_raised", compact=True,
        )
        self.metadata_check.grid(row=0, column=0, sticky="w", padx=(0, theme.px(10)), pady=theme.px(1))
        self.upscale_check = CheckRow(
            grid, theme, "允许放大", bool(self.settings["ico_upscale"]),
            command=lambda value: self._save_setting("ico_upscale", value),
            background="surface_raised", compact=True,
        )
        self.upscale_check.grid(row=0, column=1, sticky="w", pady=theme.px(1))
        self.recursive_check = CheckRow(
            grid, theme, "递归扫描子目录", bool(self.settings["recursive"]),
            command=lambda value: self._save_setting("recursive", value),
            background="surface_raised", compact=True,
        )
        self.recursive_check.grid(row=1, column=0, sticky="w", padx=(0, theme.px(10)), pady=theme.px(1))
        self.keep_tree_check = CheckRow(
            grid, theme, "保留目录结构", bool(self.settings["keep_tree"]),
            command=lambda value: self._save_setting("keep_tree", value),
            background="surface_raised", compact=True,
        )
        self.keep_tree_check.grid(row=1, column=1, sticky="w", pady=theme.px(1))

    # -- 底部 ------------------------------------------------------------
    def _build_footer(self) -> None:
        theme = self.theme
        footer = tk.Frame(self, bg=theme.color("surface_raised"))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.progress = tk.Canvas(footer, height=theme.px(3), bg=theme.color("surface_sunken"),
                                  highlightthickness=0, bd=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self._progress_bar = self.progress.create_rectangle(0, 0, 0, theme.px(3), fill=theme.color("accent"), outline="")
        self.progress.bind("<Configure>", lambda _e: self._paint_progress())

        self.status_label = tk.Label(footer, text="", bg=theme.color("surface_raised"),
                                     fg=theme.color("text_secondary"), font=theme.font("caption"), anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=theme.sp("xl"), pady=(theme.px(7), 0))

        controls = tk.Frame(footer, bg=theme.color("surface_raised"))
        controls.grid(row=2, column=0, sticky="ew", padx=theme.sp("xl"), pady=(theme.px(6), theme.px(10)))
        controls.columnconfigure(3, weight=1)

        tk.Label(controls, text="保存到", bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
                 font=theme.font("caption_bold")).grid(row=0, column=0, sticky="w", padx=(0, theme.px(8)))
        self.out_group = ChipGroup(
            controls, theme, [("源文件旁", "same"), ("指定文件夹", "folder")],
            str(self.settings["out_mode"]), self._on_out_mode_change, columns=2,
            background="surface_raised",
        )
        self.out_group.grid(row=0, column=1, sticky="w")
        self.out_dir_label = tk.Label(controls, text="", bg=theme.color("surface_raised"),
                                      fg=theme.color("text_secondary"), font=theme.font("caption"), anchor="w")
        self.out_dir_label.grid(row=0, column=2, sticky="w", padx=(theme.px(12), theme.px(8)))
        self.pick_dir_btn = TextButton(controls, theme, "选择…", self._choose_out_dir, variant="ghost")
        self.pick_dir_btn.grid(row=0, column=3, sticky="w")

        self.open_out_btn = TextButton(controls, theme, "打开输出目录", self._open_output_dir)
        self.open_out_btn.grid(row=0, column=4, sticky="e", padx=(0, theme.px(6)))
        self.open_out_btn.grid_remove()

        self.cancel_btn = TextButton(controls, theme, "取消", self._cancel_conversion)
        self.cancel_btn.grid(row=0, column=5, padx=(theme.px(6), 0))
        self.cancel_btn.grid_remove()

        self.convert_btn = tk.Button(
            controls, text="开始转换", command=self._start_conversion, font=theme.font("body_bold"),
            bg=theme.color("accent"), fg=theme.color("on_accent"),
            activebackground=theme.color("accent_hover"), activeforeground=theme.color("on_accent"),
            relief="flat", bd=0, padx=theme.px(20), pady=theme.px(7), cursor="hand2",
            highlightthickness=0,
        )
        self.convert_btn.grid(row=0, column=6, padx=(theme.px(6), 0))
        self.convert_btn.configure(state="disabled", bg=theme.color("surface_sunken"),
                                   fg=theme.color("text_disabled"))

        self._refresh_out_dir_label()

    # ============================================================ 设置逻辑 --
    def _save_setting(self, key: str, value: object, *, save: bool = True) -> None:
        self.settings[key] = value
        if save:
            self.settings.save()

    def _on_format_change(self, key: str) -> None:
        self._save_setting("target", key)
        self.format_group.set(key)
        self._sync_format_panels()

    def _sync_format_panels(self) -> None:
        target = str(self.settings["target"])
        spec = converter.FORMATS[target]
        self.format_note.configure(text=f"{spec.note}  ·  输出 .{spec.ext.lstrip('.')}")
        if spec.kind == "lossy":
            self.quality_frame.grid()
            quality = self.settings.quality(target, spec.default_quality)
            self.quality_scale.set(quality)
            self.quality_value.configure(text=str(quality))
        else:
            self.quality_frame.grid_remove()
        if target == "ico":
            self.ico_frame.grid()
        else:
            self.ico_frame.grid_remove()

    def _on_quality_move(self, value: str) -> None:
        target = str(self.settings["target"])
        quality = int(float(value))
        self.quality_value.configure(text=str(quality))
        self.settings.set_quality(target, quality)

    def _on_size_toggle(self, size: int, value: bool) -> None:
        chosen = {key for key, check in self.size_checks.items() if check.value}
        if not chosen:
            self.size_checks[size].set(True)
            self._set_status("至少保留一个图标尺寸")
            return
        self._save_setting("ico_sizes", sorted(chosen))

    def _on_fit_change(self, key: str) -> None:
        self._save_setting("ico_fit", key)
        self.fit_group.set(key)

    def _on_payload_change(self, key: str) -> None:
        self._save_setting("ico_payload", key)
        self.payload_group.set(key)
        if key == "bmp":
            self._set_status("BMP 兼容模式：给旧版 Windows 软件用，文件更大")

    def _on_out_mode_change(self, key: str) -> None:
        self._save_setting("out_mode", key)
        self.out_group.set(key)
        self._refresh_out_dir_label()

    def _refresh_out_dir_label(self) -> None:
        mode = str(self.settings["out_mode"])
        if mode == "same":
            self.out_dir_label.configure(text="与源文件同目录，重名自动加序号",
                                         fg=self.theme.color("text_secondary"))
            self.pick_dir_btn.grid_remove()
            self.keep_tree_check.set_enabled(False)
        else:
            out_dir = str(self.settings["out_dir"] or "")
            self.out_dir_label.configure(
                text=truncate(out_dir, 38) if out_dir else "还没有选择输出文件夹",
                fg=self.theme.color("text_secondary" if out_dir else "warning"),
            )
            self.pick_dir_btn.set_enabled(True)
            self.pick_dir_btn.grid()
            self.keep_tree_check.set_enabled(True)

    def _choose_out_dir(self) -> None:
        initial = str(self.settings["out_dir"] or self.settings["last_dir"] or Path.home())
        chosen = filedialog.askdirectory(parent=self, title="选择输出文件夹", initialdir=initial)
        if not chosen:
            return
        self.settings["out_dir"] = chosen
        self.settings["out_mode"] = "folder"
        self.settings.save()
        self.out_group.set("folder")
        self._refresh_out_dir_label()

    # ================================================================ 队列 --
    def _choose_files(self) -> None:
        initial = str(self.settings["last_dir"] or Path.home())
        paths = filedialog.askopenfilenames(parent=self, title="选择图片", initialdir=initial, filetypes=FILE_TYPES)
        if not paths:
            return
        self._save_setting("last_dir", str(Path(paths[0]).parent))
        self.add_paths([Path(p) for p in paths])

    def _choose_folder(self) -> None:
        if self._converting:
            self._set_status("转换进行中，先取消再修改队列")
            return
        initial = str(self.settings["last_dir"] or Path.home())
        folder = filedialog.askdirectory(parent=self, title="选择包含图片的文件夹", initialdir=initial)
        if not folder:
            return
        self._open_picker(Path(folder))

    def _open_picker(self, folder: Path) -> None:
        """打开文件夹浏览窗口，让用户自己挑要处理的图。"""
        if self._converting:
            self._set_status("转换进行中，先取消再修改队列")
            return
        self._save_setting("last_dir", str(folder))
        picker = FolderPicker(
            self, self.theme, folder,
            existing=set(self.by_path),
            recursive=bool(self.settings["recursive"]),
        )
        self._picker = picker
        self.wait_window(picker)
        self._picker = None
        if bool(self.settings["recursive"]) != picker.recursive:
            self.settings["recursive"] = picker.recursive
            self.settings.save()
        paths = list(picker.result)
        if not paths:
            self._set_status(f"已取消，未从「{folder.name or folder}」选择图片")
            return
        self.add_paths(paths)
        if picker.start_requested:
            self._pending_autostart = True
            self._try_autostart()

    def _paste_clipboard(self) -> None:
        try:
            data = ImageGrab.grabclipboard()
        except Exception:  # noqa: BLE001 - 剪贴板不可用时安静失败
            data = None
        if isinstance(data, list):
            paths = [Path(p) for p in data if Path(p).suffix.lower() in converter.INPUT_EXTS]
            if paths:
                self.add_paths(paths)
            else:
                self._set_status("剪贴板里没有可用的图片路径")
            return
        if isinstance(data, Image.Image):
            folder = Path(tempfile.gettempdir()) / "ImageForge"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"paste_{time.strftime('%Y%m%d_%H%M%S')}.png"
            try:
                data.save(dest, "PNG")
            except OSError:
                self._set_status("剪贴板图片保存失败")
                return
            self.add_paths([dest])
            return
        self._set_status("剪贴板里没有图片")

    def add_paths(self, paths: Iterable[Path]) -> None:
        if self._converting:
            self._set_status("转换进行中，先取消再修改队列")
            return
        added: list[QueueItem] = []
        skipped = 0
        for path in paths:
            try:
                path = Path(path)
                if not path.is_file():
                    if path.is_dir():
                        for nested in converter.collect_images(path, recursive=bool(self.settings["recursive"])):
                            item = self._make_item(nested)
                            if item:
                                added.append(item)
                        continue
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            if path.suffix.lower() not in converter.INPUT_EXTS:
                skipped += 1
                continue
            item = self._make_item(path)
            if item:
                added.append(item)
        if not added:
            self._set_status("没有可用的图片（支持 PNG / JPG / WebP / GIF / BMP / TIFF / AVIF / ICO）")
            return
        self._update_summary()
        self._set_status(f"已加入 {len(added)} 个文件" + (f"，跳过 {skipped} 个不支持的文件" if skipped else ""))
        self._start_probe_worker()

    def _make_item(self, path: Path) -> Optional[QueueItem]:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in self.by_path:
            return None
        if not self.items:
            self.empty_frame.grid_remove()
            self.scroll.grid()
        row = QueueRow(
            self.scroll.inner, self.theme,
            name=path.name,
            path_text=str(path.parent),
            on_select=self._select_item,
            on_open=self._open_preview,
            on_remove=self._remove_item,
            on_menu=self._show_row_menu,
        )
        row.pack(fill="x")
        item = QueueItem(path=path, row=row)
        try:
            item.size = path.stat().st_size
        except OSError:
            item.size = 0
        row.set_status("reading", "读取中…")
        row.set_meta(f"{converter.human_size(item.size)} · 读取尺寸…")
        self.items.append(item)
        self.by_path[key] = item
        self._probe_pending.append(item)
        return item

    def _start_probe_worker(self) -> None:
        if not self._probe_pending:
            return
        if self._probe_thread and self._probe_thread.is_alive():
            return
        batch, self._probe_pending = self._probe_pending[:], []
        self._probe_thread = threading.Thread(target=self._probe_worker, args=(batch,), daemon=True)
        self._probe_thread.start()
        self.after(40, self._drain_probe_queue)

    def _probe_worker(self, batch: list[QueueItem]) -> None:
        for item in batch:
            info = converter.probe(item.path)
            thumb = None
            if info.ok:
                try:
                    with Image.open(item.path) as img:
                        thumb = img.convert("RGBA")
                        thumb.thumbnail((128, 128), Image.Resampling.LANCZOS)
                except Exception:  # noqa: BLE001
                    thumb = None
            self._probe_queue.put(("probe", item, info, thumb))

    def _drain_probe_queue(self) -> None:
        while True:
            try:
                kind, *payload = self._probe_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "probe":
                item, info, thumb = payload
                self._apply_probe(item, info, thumb)
        self._update_summary()
        pending = (self._probe_thread is not None and self._probe_thread.is_alive()) or not self._probe_queue.empty()
        if pending:
            self.after(60, self._drain_probe_queue)
        elif self._probe_pending:
            self._start_probe_worker()
        else:
            self._try_autostart()

    def _try_autostart(self) -> None:
        """「添加并转换」：等队列里的图都探测完，再自动开始转换。"""
        if not self._pending_autostart or self._converting:
            return
        if self._probe_pending or not self.items:
            return
        self._pending_autostart = False
        self.after(80, self._start_conversion)

    def _apply_probe(self, item: QueueItem, info: ImageInfo, thumb: Optional[Image.Image]) -> None:
        if item not in self.items:
            return
        item.info = info
        item.thumb = thumb
        if not info.ok:
            item.status = "failed"
            item.message = info.message
            item.row.set_status("failed", "无法读取", info.message)
            item.row.set_meta(f"{converter.human_size(item.size)} · {info.message}")
            return
        item.status = "idle"
        bits = [converter.human_size(item.size), info.format or "?", f"{info.width}×{info.height}"]
        if info.frames > 1:
            bits.append(f"{info.frames} 帧")
        if info.has_alpha:
            bits.append("透明")
        item.row.set_meta(" · ".join(bits))
        item.row.set_status("idle", "待转换")
        if thumb is not None:
            item.row.set_thumb(thumb)
        if self.selected is None:
            self._select_item(item)

    def _select_item(self, item: QueueItem) -> None:
        if self.selected is item:
            return
        if self.selected is not None:
            self.selected.row.set_selected(False)
        self.selected = item
        item.row.set_selected(True)
        self._update_convert_button()

    def _remove_item(self, item: QueueItem) -> None:
        if self._converting:
            self._set_status("转换进行中，先取消再修改队列")
            return
        if item in self.items:
            self.items.remove(item)
        try:
            key = str(item.path.resolve())
        except OSError:
            key = str(item.path)
        self.by_path.pop(key, None)
        if self.selected is item:
            self.selected = None
        item.row.destroy()
        if self.items and self.selected is None:
            self._select_item(self.items[-1])
        if not self.items:
            self.scroll.grid_remove()
            self.empty_frame.grid()
        self._update_summary()

    def _clear_all(self) -> None:
        if self._converting:
            self._set_status("转换进行中，先取消再清空")
            return
        for item in list(self.items):
            item.row.destroy()
        self.items.clear()
        self.by_path.clear()
        self.selected = None
        self._done = self._failed = self._total = 0
        self.scroll.grid_remove()
        self.empty_frame.grid()
        self._update_summary()
        self._set_status("列表已清空")

    def _show_row_menu(self, item: QueueItem, event: tk.Event) -> None:
        self._select_item(item)
        menu = tk.Menu(self, tearoff=0, bg=self.theme.color("surface_raised"),
                       fg=self.theme.color("text_primary"), activebackground=self.theme.color("accent_subtle"),
                       activeforeground=self.theme.color("text_primary"), bd=0, relief="flat")
        menu.add_command(label="预览", command=lambda: self._open_preview(item))
        menu.add_command(label="在资源管理器中显示", command=lambda: reveal_file(item.path))
        menu.add_separator()
        menu.add_command(label="从队列移除", command=lambda: self._remove_item(item))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()

    def _update_summary(self) -> None:
        total = len(self.items)
        bytes_total = sum(item.size for item in self.items)
        self.count_label.configure(text=f"{total} 个文件" if total else "")
        self.summary_label.configure(text=f"{total} 个 · {converter.human_size(bytes_total)}" if total else "")
        self.clear_btn.configure(state="normal" if total else "disabled",
                                 fg=self.theme.color("text_secondary" if total else "text_disabled"))
        self._update_convert_button()

    def _update_convert_button(self) -> None:
        theme = self.theme
        if self._converting:
            return
        ready = [item for item in self.items if item.status in ("idle", "failed", "done")]
        if ready:
            self.convert_btn.configure(text=f"开始转换 · {len(ready)}", state="normal",
                                       bg=theme.color("accent"), fg=theme.color("on_accent"))
        else:
            self.convert_btn.configure(text="开始转换", state="disabled",
                                       bg=theme.color("surface_sunken"), fg=theme.color("text_disabled"))

    # ============================================================ 转换流程 --
    def _build_options(self) -> ConvertOptions:
        target = str(self.settings["target"])
        spec = converter.FORMATS[target]
        return ConvertOptions(
            target=target,
            quality=self.settings.quality(target, spec.default_quality),
            lossless=False,
            background=(255, 255, 255),
            ico_sizes=self.settings.sizes(),
            ico_fit=str(self.settings["ico_fit"]),
            ico_payload=str(self.settings["ico_payload"]),
            ico_upscale=bool(self.settings["ico_upscale"]),
            keep_metadata=bool(self.settings["keep_metadata"]),
            overwrite=False,
            suffix=str(self.settings["suffix"] or ""),
        )

    def _start_conversion(self) -> None:
        if self._converting:
            return
        targets = [item for item in self.items if item.status in ("idle", "failed", "done")]
        if not targets:
            self._set_status("没有待转换的文件")
            return
        out_root: Optional[Path] = None
        if str(self.settings["out_mode"]) == "folder":
            raw = str(self.settings["out_dir"] or "")
            if not raw:
                self._choose_out_dir()
                raw = str(self.settings["out_dir"] or "")
                if not raw:
                    self._set_status("请先选择输出文件夹")
                    return
            out_root = Path(raw)
            try:
                out_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("无法写入输出文件夹", str(exc), parent=self)
                return

        options = self._build_options()
        self.settings.save()

        self._converting = True
        self._cancel.clear()
        self._done = self._failed = 0
        self._total = len(targets)
        self._last_out_dir = out_root
        for item in targets:
            item.status = "pending"
            item.result = None
            item.row.set_status("idle", "等待中")
        self.cancel_btn.set_enabled(True)
        self.cancel_btn.grid()
        self._paint_progress(0, self._total)
        self.convert_btn.configure(text="转换中…", state="disabled",
                                   bg=self.theme.color("surface_sunken"), fg=self.theme.color("text_disabled"))
        for widget in (self.add_files_btn, self.add_folder_btn, self.paste_btn, self.clear_btn):
            widget.configure(state="disabled")
        self.format_group.set_enabled(False)
        self.out_group.set_enabled(False)
        self._set_status(f"正在转换 0/{self._total}…")

        self._convert_thread = threading.Thread(
            target=self._convert_worker,
            args=(targets, options, out_root, bool(self.settings["keep_tree"])),
            daemon=True,
        )
        self._convert_thread.start()
        self.after(60, self._drain_convert_queue)

    def _convert_worker(self, targets: list[QueueItem], options: ConvertOptions,
                        out_root: Optional[Path], keep_tree: bool) -> None:
        paths = [item.path for item in targets]
        base_dir = None
        if keep_tree and out_root is not None and paths:
            try:
                base_dir = Path(os.path.commonpath([str(p.parent) for p in paths]))
            except ValueError:
                base_dir = None

        def on_result(index: int, result: ConvertResult) -> None:
            self._convert_queue.put(("result", index, result))

        converter.run_batch(paths, options, out_root, keep_tree=keep_tree, base_dir=base_dir,
                            on_result=on_result, should_cancel=self._cancel.is_set)
        self._convert_queue.put(("done", None, None))

    def _drain_convert_queue(self) -> None:
        finished = False
        while True:
            try:
                kind, _index, result = self._convert_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                finished = True
                continue
            self._apply_result(result)
        if finished:
            self._finish_conversion()
        elif (self._convert_thread is not None and self._convert_thread.is_alive()) or not self._convert_queue.empty():
            self.after(60, self._drain_convert_queue)

    def _apply_result(self, result: ConvertResult) -> None:
        item = None
        try:
            item = self.by_path.get(str(result.src.resolve()))
        except OSError:
            item = None
        if item is None:
            return
        item.result = result
        if result.ok:
            item.status = "done"
            self._done += 1
            extra = "（重名已改名）" if result.renamed else ""
            item.row.set_status("done", "完成", f"输出到 {result.dest}{extra}")
            if result.dest is not None:
                dest = result.dest
                if dest.suffix.lower() == ".ico":
                    try:
                        count = len(ico.read_entries(dest))
                    except (OSError, ico.IconError):
                        count = 0
                    head = f"{count} 个尺寸" if count else "ICO 图标"
                else:
                    head = f"{result.width}×{result.height}"
                bits = [head, converter.human_size(result.bytes)]
                if result.frames > 1:
                    bits.append(f"{result.frames} 帧")
                if result.warnings:
                    bits.append("；".join(result.warnings))
                item.row.set_meta(" · ".join(bits) + extra)
        else:
            item.status = "failed"
            self._failed += 1
            item.row.set_status("failed", "失败", result.message)
            item.row.set_meta(result.message)
        self._set_status(f"正在转换 {self._done + self._failed}/{self._total}…")
        self._paint_progress(self._done + self._failed, self._total)

    def _finish_conversion(self) -> None:
        self._converting = False
        self.cancel_btn.grid_remove()
        self.convert_btn.configure(state="normal")
        for widget in (self.add_files_btn, self.add_folder_btn, self.paste_btn, self.clear_btn):
            widget.configure(state="normal")
        self.format_group.set_enabled(True)
        self.out_group.set_enabled(True)
        self._update_convert_button()
        self._paint_progress(1, 1)
        if self._done or self._failed:
            where = ""
            if self._last_out_dir is not None:
                where = f" · 输出到 {self._last_out_dir}"
            else:
                where = " · 输出到源文件所在目录"
            self._set_status(f"完成 {self._done} · 失败 {self._failed}{where}")
            self.open_out_btn.grid()
        else:
            self._set_status("已取消")
        self._update_summary()

    def _cancel_conversion(self) -> None:
        if not self._converting:
            return
        self._cancel.set()
        self.cancel_btn.set_enabled(False)
        self._set_status("正在取消…")

    def _paint_progress(self, done: int | float = None, total: int | float = None) -> None:
        if done is None:
            done, total = self._done + self._failed, max(1, self._total or len(self.items))
        width = max(1, self.progress.winfo_width())
        ratio = 0.0 if not total else min(1.0, float(done) / float(total))
        self.progress.coords(self._progress_bar, 0, 0, width * ratio, self.theme.px(3))
        self.progress.itemconfigure(self._progress_bar, fill=self.theme.color("accent"))

    def _open_output_dir(self) -> None:
        target = self._last_out_dir
        if target is None:
            done = next((item for item in self.items if item.status == "done" and item.result and item.result.dest), None)
            if done is not None and done.result and done.result.dest:
                target = done.result.dest.parent
        if target is not None:
            open_folder(Path(target))

    # ============================================================ 预览窗口 --
    def _open_preview(self, item: QueueItem) -> None:
        if not item.info or not item.info.ok:
            self._set_status(f"无法预览：{item.message or '文件不可读'}")
            return
        PreviewWindow(self, item)

    # ================================================================ 退出 --
    def _on_close(self) -> None:
        try:
            width = self.winfo_width() / self.theme.scale
            height = self.winfo_height() / self.theme.scale
            self.settings["window"] = f"{int(width)}x{int(height)}"
        except tk.TclError:
            pass
        self.settings.save()
        self.destroy()

    def _toggle_theme(self) -> None:
        self.settings["theme"] = "dark" if self.theme.mode == "light" else "light"
        self.settings.save()
        self.restart_theme = True
        self.destroy()

    # ============================================================== 快捷键 --
    def _bind_keys(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._choose_files())
        self.bind_all("<Control-O>", lambda _e: self._choose_folder())
        self.bind_all("<Control-v>", lambda _e: self._paste_clipboard())
        self.bind_all("<Control-V>", lambda _e: self._paste_clipboard())
        self.bind_all("<Delete>", lambda _e: self._remove_item(self.selected) if self.selected else None)
        self.bind_all("<Control-Return>", lambda _e: self._start_conversion())
        self.bind_all("<Return>", self._on_return)
        self.bind_all("<Escape>", lambda _e: self._cancel_conversion())
        self.bind_all("<Up>", lambda _e: self._move_selection(-1))
        self.bind_all("<Down>", lambda _e: self._move_selection(1))

    def _on_return(self, _event: tk.Event) -> None:
        if self.selected is not None:
            self._open_preview(self.selected)

    def _move_selection(self, step: int) -> None:
        if not self.items:
            return
        if self.selected is None or self.selected not in self.items:
            self._select_item(self.items[0])
            return
        index = max(0, min(len(self.items) - 1, self.items.index(self.selected) + step))
        item = self.items[index]
        self._select_item(item)
        self._ensure_visible(item)

    def _ensure_visible(self, item: QueueItem) -> None:
        try:
            self.scroll.inner.update_idletasks()
            total = self.scroll.inner.winfo_height()
            view_height = self.scroll.canvas.winfo_height()
            if total <= 0 or view_height <= 0:
                return
            y = item.row.winfo_y()
            height = item.row.winfo_height()
            first = self.scroll.canvas.canvasy(0)
            if y < first:
                self.scroll.canvas.yview_moveto(max(0.0, y / total))
            elif y + height > first + view_height:
                self.scroll.canvas.yview_moveto(min(1.0, (y + height - view_height) / total))
        except tk.TclError:
            pass

    # ================================================================ 拖拽 --
    def _register_dnd(self) -> None:
        if not DND_AVAILABLE:
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.dnd_bind("<<DropEnter>>", lambda _e: self.drop_zone.set_active(True))
            self.dnd_bind("<<DropLeave>>", lambda _e: self.drop_zone.set_active(False))
        except tk.TclError:
            pass

    def _on_drop(self, event: tk.Event) -> None:
        self.drop_zone.set_active(False)
        try:
            raw = self.tk.splitlist(event.data)
        except tk.TclError:
            raw = str(event.data).split()
        paths = [Path(item) for item in raw]
        files: list[Path] = []
        folders: list[Path] = []
        for path in paths:
            try:
                (folders if path.is_dir() else files).append(path)
            except OSError:
                continue
        if files:
            self.add_paths(files)
        if folders:
            if len(folders) > 1:
                self._set_status("一次只浏览一个文件夹，已打开第一个")
            self._open_picker(folders[0])

    # ================================================================ 杂项 --
    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)


class PreviewWindow(tk.Toplevel):
    """预览：大图 + 元信息 + ICO 各尺寸的真实渲染。"""

    def __init__(self, master: App, item: QueueItem) -> None:
        super().__init__(master)
        self.app = master
        self.theme = master.theme
        self.item = item
        theme = self.theme
        self.title(f"预览 · {item.path.name}")
        self.configure(bg=theme.color("surface"))
        width, height = theme.px(780), theme.px(560)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(theme.px(560), theme.px(420))
        self.transient(master)
        try:
            self.iconbitmap(default=str(ASSETS / "app.ico"))
        except tk.TclError:
            pass

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = tk.Frame(self, bg=theme.color("surface"))
        head.grid(row=0, column=0, sticky="ew", padx=theme.sp("lg"), pady=(theme.sp("md"), theme.sp("sm")))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text=item.path.name, bg=theme.color("surface"), fg=theme.color("text_primary"),
                 font=theme.font("h1"), anchor="w").grid(row=0, column=0, sticky="w")
        info = item.info
        if info is not None:
            bits = [f"{info.width}×{info.height}", info.format, info.mode, converter.human_size(item.size)]
            if info.frames > 1:
                bits.append(f"{info.frames} 帧")
            if info.has_alpha:
                bits.append("含透明通道")
            tk.Label(head, text=" · ".join(str(b) for b in bits if b), bg=theme.color("surface"),
                     fg=theme.color("text_secondary"), font=theme.font("caption"),
                     anchor="w").grid(row=1, column=0, sticky="w")

        self.canvas = tk.Canvas(self, bg=theme.color("surface_sunken"), highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=theme.sp("lg"))
        self.canvas.bind("<Configure>", lambda _e: self._render())
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._source: Optional[Image.Image] = None
        try:
            with Image.open(item.path) as img:
                img.seek(0)
                self._source = img.convert("RGBA")
        except Exception:  # noqa: BLE001
            self._source = None

        footer = tk.Frame(self, bg=theme.color("surface_raised"))
        footer.grid(row=2, column=0, sticky="ew", pady=(theme.sp("md"), 0))
        footer.columnconfigure(0, weight=1)
        inner = tk.Frame(footer, bg=theme.color("surface_raised"))
        inner.grid(row=0, column=0, sticky="ew", padx=theme.sp("lg"), pady=theme.sp("md"))
        inner.columnconfigure(0, weight=1)
        self.size_canvas = tk.Canvas(inner, height=theme.px(118), bg=theme.color("surface_raised"),
                                     highlightthickness=0, bd=0)
        self.size_canvas.grid(row=0, column=0, sticky="ew")
        actions = tk.Frame(inner, bg=theme.color("surface_raised"))
        actions.grid(row=1, column=0, sticky="ew", pady=(theme.px(6), 0))
        actions.columnconfigure(0, weight=1)
        reveal = tk.Button(
            actions, text="在资源管理器中显示", command=lambda: reveal_file(item.path), font=theme.font("body"),
            bg=theme.color("surface_raised"), fg=theme.color("text_primary"),
            activebackground=theme.color("surface_hover"), activeforeground=theme.color("text_primary"),
            relief="flat", bd=0, padx=theme.px(12), pady=theme.px(5), cursor="hand2",
            highlightthickness=1, highlightbackground=theme.color("border_strong"),
            highlightcolor=theme.color("focus"),
        )
        reveal.grid(row=0, column=0, sticky="w")
        master._attach_button_hover(reveal)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(30, self._render_sizes)
        self.after(120, self._render)
        self.after(320, self._render)
        self.after(300, lambda: apply_title_bar_theme(self, theme.mode == "dark"))

    # -- 渲染 -----------------------------------------------------------
    def _render(self) -> None:
        theme = self.theme
        self.canvas.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 10 or height < 10:
            return
        tile = theme.px(8)
        for y in range(0, height, tile):
            for x in range(0, width, tile):
                shade = theme.color("checker_a") if ((x // tile) + (y // tile)) % 2 == 0 else theme.color("checker_b")
                self.canvas.create_rectangle(x, y, x + tile, y + tile, fill=shade, outline="")
        if self._source is None:
            self.canvas.create_text(width / 2, height / 2, text="无法读取图片数据",
                                    fill=theme.color("text_secondary"), font=theme.font("body"))
            return
        pad = theme.px(12)
        image = self._source.copy()
        image.thumbnail((max(1, width - pad * 2), max(1, height - pad * 2)), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=self)
        self._photo = photo
        self.canvas.create_image(width / 2, height / 2, image=photo, anchor="center")
        self.canvas.create_rectangle(
            (width - image.width) / 2 - 1, (height - image.height) / 2 - 1,
            (width + image.width) / 2, (height + image.height) / 2,
            outline=theme.color("border"), width=1,
        )

    def _render_sizes(self) -> None:
        theme = self.theme
        self.size_canvas.delete("all")
        if self._source is None:
            return
        target = str(self.app.settings["target"])
        if target != "ico":
            self.size_canvas.create_text(
                theme.px(2), theme.px(34), anchor="w",
                text=f"输出 {converter.FORMATS[target].label}：只导出单张图片，不生成多尺寸。",
                fill=theme.color("text_secondary"), font=theme.font("caption"),
            )
            return
        sizes = self.app.settings.sizes()
        try:
            frames = ico.render_frames(self._source, sizes, fit=str(self.app.settings["ico_fit"]),
                                       upscale=bool(self.app.settings["ico_upscale"]))
        except ico.IconError as exc:
            self.size_canvas.create_text(theme.px(2), theme.px(34), anchor="w", text=str(exc),
                                         fill=theme.color("warning"), font=theme.font("caption"))
            return
        self.size_canvas.create_text(theme.px(2), theme.px(2), anchor="nw",
                                     text="各尺寸的渲染效果（小图标放大 3×，128/256 缩到 88px 显示，标注见下）：",
                                     fill=theme.color("text_secondary"), font=theme.font("caption"))
        x = theme.px(4)
        top = theme.px(24)
        for size, frame in frames:
            if size <= 32:
                side, note = size * 3, "×3"
            elif size <= 88:
                side, note = size, ""
            else:
                side, note = 88, "↓"
            shown = frame.resize((side, side),
                                 Image.Resampling.NEAREST if side >= size else Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(shown, master=self.size_canvas)
            setattr(self, f"_size_photo_{size}", photo)
            self.size_canvas.create_image(x, top, image=photo, anchor="nw")
            self.size_canvas.create_rectangle(x, top, x + side, top + side, outline=theme.color("border"))
            label = f"{size}px" + (f" {note}" if note else "")
            self.size_canvas.create_text(x, top + side + theme.px(5), anchor="nw", text=label,
                                         fill=theme.color("text_secondary"), font=theme.font("mono"))
            x += side + theme.px(16)


def main(argv: Optional[list[str]] = None) -> int:
    enable_dpi_awareness()
    set_app_user_model_id()
    if not single_instance.acquire():
        # 已经在跑了：把那个窗口叫到前台，这次启动直接退出
        single_instance.focus_existing()
        return 0
    settings = Settings.load()
    while True:
        app = App(settings)
        app.mainloop()
        if not app.restart_theme:
            break
        try:
            app.destroy()
        except tk.TclError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())