"""从文件夹里挑图：搜索、多选，再一次性加入队列。"""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

from PIL import Image, ImageTk

from . import converter
from .theme import Theme
from .widgets import CheckRow, IconButton, ScrollFrame, TextButton, Tooltip, round_rect, truncate

ASSETS = Path(__file__).resolve().parent / "assets"

MAX_ROWS = 600          # 最多渲染多少行，避免几千张图时窗口卡死
ROW_HEIGHT = 44
THUMB_SIDE = 30
BUILD_CHUNK = 40        # 每次事件循环建多少行
THUMB_BATCH = 6         # 每次轮询贴多少张缩略图
SEARCH_DELAY = 180

_PLACEHOLDER = "搜索文件名…"


def natural_key(name: str) -> list:
    """宽松自然排序：2.jpg 排在 10.jpg 前面。"""
    parts = re.split(r"(\d+)", name.casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def matches(name: str, query: str) -> bool:
    """文件名匹配：忽略大小写，空格分隔的多个关键词需全部命中。"""
    if not query:
        return True
    haystack = name.casefold()
    return all(word in haystack for word in query.casefold().split())


def _fit_text(font, text: str, max_width: int) -> str:
    """按像素宽度截断，省略号保证能塞下。"""
    if max_width <= 0 or font.measure(text) <= max_width:
        return text
    ellipsis_width = font.measure("…")
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid]) + ellipsis_width <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


class PickerRow(tk.Canvas):
    """一行：勾选框 + 缩略图 + 文件名 + 规格。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        path: Path,
        *,
        size: int,
        taken: bool,
        on_toggle,
        on_activate,
    ) -> None:
        self.theme = theme
        self.path = path
        self.checked = False
        self.taken = taken
        self.focused = False
        self._hover = False
        self._thumb_image: Optional[ImageTk.PhotoImage] = None
        self._meta = converter.human_size(size)
        self._on_toggle = on_toggle
        self._on_activate = on_activate
        super().__init__(
            master,
            height=theme.px(ROW_HEIGHT),
            bg=theme.color("surface_raised"),
            highlightthickness=0,
            bd=0,
        )
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self.bind("<Double-Button-1>", lambda _e: self._on_activate(self))
        self.bind("<Configure>", self._on_configure)
        self._draw()

    # -- 外部接口 -------------------------------------------------------
    def set_checked(self, value: bool) -> None:
        if self.checked != value:
            self.checked = value
            self._draw()

    def set_focused(self, value: bool) -> None:
        if self.focused != value:
            self.focused = value
            self._draw()

    def set_thumb(self, image: Optional[Image.Image], meta: Optional[str] = None) -> None:
        if image is not None:
            side = self.theme.px(THUMB_SIDE)
            thumb = image.copy()
            thumb.thumbnail((side, side), Image.Resampling.LANCZOS)
            self._thumb_image = ImageTk.PhotoImage(thumb, master=self)
        if meta:
            self._meta = meta
        self._draw()

    def destroy(self) -> None:  # 退出时缩略图是最后一批被回收的对象
        self._thumb_image = None
        super().destroy()

    # -- 交互 -----------------------------------------------------------
    def _click(self, event: tk.Event) -> None:
        self._on_toggle(self, bool(event.state & 0x0001))

    def _set_hover(self, value: bool) -> None:
        if self._hover != value:
            self._hover = value
            self._draw()

    def _on_configure(self, _event: tk.Event) -> None:
        self._draw()

    # -- 绘制 -----------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        theme = self.theme
        height = theme.px(ROW_HEIGHT)
        width = max(self.winfo_width(), theme.px(320))
        if self.taken:
            background = theme.color("surface_raised")
        elif self.checked:
            background = theme.color("accent_subtle")
        elif self._hover:
            background = theme.color("surface_hover")
        else:
            background = theme.color("surface_raised")
        self.configure(bg=background)
        self.create_line(0, height - 1, width, height - 1, fill=theme.color("border"))
        if self.focused:
            self.create_rectangle(0, 0, theme.px(2), height, fill=theme.color("accent"), outline="")

        # 勾选框
        box = theme.px(18)
        box_x = theme.px(12)
        box_y = (height - box) / 2
        if self.checked:
            fill = outline = theme.color("accent")
        else:
            fill = theme.color("surface_raised")
            outline = theme.color("border_strong")
        round_rect(self, box_x, box_y, box_x + box, box_y + box, theme.px(4), fill=fill, outline=outline, width=1)
        if self.checked:
            tick = max(1, theme.px(2))
            self.create_line(
                box_x + box * 0.26, box_y + box * 0.53,
                box_x + box * 0.44, box_y + box * 0.72,
                box_x + box * 0.76, box_y + box * 0.30,
                fill=theme.color("on_accent"), width=tick, capstyle="round", joinstyle="round",
            )

        # 缩略图
        side = theme.px(THUMB_SIDE)
        thumb_x = theme.px(40)
        thumb_y = (height - side) / 2
        round_rect(self, thumb_x, thumb_y, thumb_x + side, thumb_y + side, theme.px(4),
                   fill=theme.color("surface_sunken"), outline=theme.color("border"))
        if self._thumb_image is not None:
            self.create_image(thumb_x + side / 2, thumb_y + side / 2, image=self._thumb_image)

        # 文字
        text_x = theme.px(80)
        name_font = theme.font("body")
        meta_font = theme.font("caption")
        badge = theme.font("caption_bold").measure("已在列表") if self.taken else 0
        max_width = max(theme.px(60), width - text_x - theme.px(12) - badge - (theme.px(8) if self.taken else 0))
        name_color = "text_disabled" if self.taken else "text_primary"
        self.create_text(
            text_x, height / 2 - theme.px(8), text=_fit_text(name_font, self.path.name, max_width),
            anchor="w", font=name_font, fill=theme.color(name_color),
        )
        self.create_text(
            text_x, height / 2 + theme.px(8), text=_fit_text(meta_font, self._meta, max_width),
            anchor="w", font=meta_font, fill=theme.color("text_secondary"),
        )
        if self.taken:
            self.create_text(
                width - theme.px(12), height / 2, text="已在列表",
                anchor="e", font=theme.font("caption_bold"), fill=theme.color("text_secondary"),
            )


class FolderPicker(tk.Toplevel):
    """浏览一个文件夹，手动勾选要处理的图片。

    确认后 ``result`` 里是选中的路径，``start_requested`` 表示是否要立即开始转换。
    """

    def __init__(self, master: tk.Misc, theme: Theme, folder: Path, *, existing, recursive: bool = False) -> None:
        super().__init__(master)
        self.theme = theme
        self.folder = Path(folder)
        self.recursive = recursive
        self.existing = {str(p) for p in existing}
        self.result: list[Path] = []
        self.start_requested = False

        self._rows: dict[Path, PickerRow] = {}
        self._build_paths: list[Path] = []
        self._visible: list[Path] = []
        self._checked: set[Path] = set()
        self._found: list[Path] = []
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._scanning = False
        self._building = False
        self._building_cursor = 0
        self._thumbs_running = False
        self._focus_index: Optional[int] = None
        self._focus_path: Optional[Path] = None
        self._anchor: Optional[int] = None
        self._alive = True
        self._placeholder_on = True
        self._search_job: Optional[str] = None

        self.title("选择图片 · ImageForge")
        self.configure(bg=theme.color("surface"))
        width, height = theme.px(920), theme.px(660)
        self._place_over(master, width, height)
        self.minsize(theme.px(720), theme.px(470))
        self.transient(master)
        try:
            self.iconbitmap(default=str(ASSETS / "app.ico"))
        except tk.TclError:
            pass

        outer = tk.Frame(self, bg=theme.color("surface"))
        outer.pack(fill="both", expand=True, padx=theme.px(16), pady=theme.px(14))
        self._build_head(outer)
        self._build_filter(outer)
        self._build_list(outer)
        self._build_footer(outer)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._confirm(False))
        self.bind("<Control-Return>", lambda _e: self._confirm(True))
        self.bind("<Up>", self._key_move)
        self.bind("<Down>", self._key_move)
        self.bind("<space>", self._key_toggle)
        self.bind("<Control-a>", self._key_select_all)

        self.grab_set()
        self.after(30, self._scan)
        self.after(60, self._pump)
        self.after(200, self._apply_titlebar)
        self.search_entry.focus_set()

    # ================================================================ 布局 --
    def _place_over(self, master: tk.Misc, width: int, height: int) -> None:
        try:
            master.update_idletasks()
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
            x, y = mx + (mw - width) // 2, my + (mh - height) // 2
        except tk.TclError:
            x = y = 0
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_head(self, outer: tk.Frame) -> None:
        theme = self.theme
        head = tk.Frame(outer, bg=theme.color("surface"))
        head.pack(fill="x")
        self.folder_icon = IconButton(head, theme, "folder", lambda: None, background="surface",
                                      size=theme.px(22), tooltip="当前文件夹")
        self.folder_icon.pack(side="left", padx=(0, theme.px(4)))
        self.folder_label = tk.Label(
            head, text=truncate(str(self.folder), 62), bg=theme.color("surface"),
            fg=theme.color("text_primary"), font=theme.font("h2"), anchor="w",
        )
        self.folder_label.pack(side="left")
        Tooltip(self.folder_label, lambda: str(self.folder), theme)
        self.change_btn = TextButton(head, theme, "更改文件夹…", self._change_folder,
                                     variant="ghost", background="surface")
        self.change_btn.pack(side="right")
        self.refresh_btn = TextButton(head, theme, "刷新", self._scan, variant="ghost", background="surface")
        self.refresh_btn.pack(side="right")

    def _build_filter(self, outer: tk.Frame) -> None:
        theme = self.theme
        bar = tk.Frame(outer, bg=theme.color("surface"))
        bar.pack(fill="x", pady=(theme.px(12), theme.px(8)))

        search_frame = tk.Frame(
            bar, bg=theme.color("surface_raised"),
            highlightthickness=1, highlightbackground=theme.color("border"),
            highlightcolor=theme.color("accent"),
        )
        search_frame.pack(side="left")
        self._search_frame = search_frame
        self.search_var = tk.StringVar(master=self)
        self.search_entry = tk.Entry(
            search_frame, textvariable=self.search_var, bd=0, relief="flat", width=24,
            bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
            insertbackground=theme.color("text_primary"), font=theme.font("body"),
        )
        self.search_entry.pack(padx=theme.px(8), pady=theme.px(6))
        self.search_entry.insert(0, _PLACEHOLDER)
        self.search_entry.bind("<KeyPress>", self._search_key_press)
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_var.trace_add("write", self._search_changed)

        self.recursive_check = CheckRow(
            bar, theme, "包含子文件夹", self.recursive, self._toggle_recursive,
            background="surface", compact=True,
        )
        self.recursive_check.pack(side="left", padx=(theme.px(14), 0))

        self.clear_btn = TextButton(bar, theme, "清空", lambda: self._select_none(), variant="ghost", background="surface")
        self.clear_btn.pack(side="right")
        self.all_btn = TextButton(bar, theme, "全选", self._select_all, variant="ghost", background="surface")
        self.all_btn.pack(side="right")

    def _build_list(self, outer: tk.Frame) -> None:
        theme = self.theme
        card = tk.Frame(
            outer, bg=theme.color("surface_raised"),
            highlightthickness=1, highlightbackground=theme.color("border"),
        )
        card.pack(fill="both", expand=True)
        self.scroll = ScrollFrame(card, theme, background="surface_raised")
        self.scroll.pack(fill="both", expand=True)
        self.empty_label = tk.Label(
            self.scroll.inner, text="正在扫描…", bg=theme.color("surface_raised"),
            fg=theme.color("text_secondary"), font=theme.font("body"),
        )
        self.empty_label.pack(fill="x", pady=theme.px(40))

    def _build_footer(self, outer: tk.Frame) -> None:
        theme = self.theme
        foot = tk.Frame(outer, bg=theme.color("surface"))
        foot.pack(fill="x", pady=(theme.px(12), 0))
        self.count_label = tk.Label(
            foot, text="正在扫描…", bg=theme.color("surface"),
            fg=theme.color("text_secondary"), font=theme.font("body"), anchor="w",
        )
        self.count_label.pack(side="left")
        self.cancel_btn = TextButton(foot, theme, "取消", self._cancel, variant="ghost", background="surface")
        self.cancel_btn.pack(side="right")
        self.start_btn = TextButton(foot, theme, "添加并转换", lambda: self._confirm(True),
                                    variant="secondary", background="surface")
        self.start_btn.pack(side="right", padx=(0, theme.px(8)))
        self.confirm_btn = TextButton(foot, theme, "添加选中", lambda: self._confirm(False),
                                      variant="primary", background="surface", min_width=theme.px(132))
        self.confirm_btn.pack(side="right", padx=(0, theme.px(8)))
        self.confirm_btn.set_enabled(False)
        self.start_btn.set_enabled(False)

    # ================================================================ 扫描 --
    def _scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._found = []
        self._build_paths = []
        self._visible = []
        self._checked.clear()
        self._focus_index = None
        self._focus_path = None
        self._anchor = None
        self._building = False
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self.empty_label.configure(text="正在扫描…", fg=self.theme.color("text_secondary"))
        self.empty_label.pack(fill="x", pady=self.theme.px(40))
        self._update_footer()
        threading.Thread(target=self._scan_worker, args=(self.folder, self.recursive), daemon=True).start()

    def _scan_worker(self, folder: Path, recursive: bool) -> None:
        try:
            found = converter.collect_images(folder, recursive=recursive)
        except OSError:
            found = []
        self._events.put(("scan", found))

    def _start_build(self) -> None:
        found = self._found
        if not found:
            self.empty_label.configure(text="这个文件夹里没有支持的图片")
            self._update_footer()
            return
        self.empty_label.pack_forget()
        found.sort(key=lambda p: (len(p.relative_to(self.folder).parts) > 1, natural_key(p.name)))
        self._build_paths = found[:MAX_ROWS]
        self._building = True
        self._building_cursor = 0
        self._build_chunk()

    def _build_chunk(self) -> None:
        if not self._alive:
            return
        end = min(self._building_cursor + BUILD_CHUNK, len(self._build_paths))
        query = self._query()
        for path in self._build_paths[self._building_cursor:end]:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            row = PickerRow(
                self.scroll.inner, self.theme, path,
                size=size,
                taken=str(path.resolve()) in self.existing,
                on_toggle=self._toggle,
                on_activate=self._activate,
            )
            row.pack(fill="x")
            self._rows[path] = row
            if not matches(path.name, query):
                row.pack_forget()
        self._building_cursor = end
        if end < len(self._build_paths):
            self.after(1, self._build_chunk)
        else:
            self._building = False
            self._apply_filter(keep_scroll=False)
            self._start_thumbs(list(self._build_paths))

    def _start_thumbs(self, paths: list[Path]) -> None:
        if self._thumbs_running:
            return
        self._thumbs_running = True
        side = self.theme.px(THUMB_SIDE)
        threading.Thread(target=self._thumb_worker, args=(paths, side), daemon=True).start()

    def _thumb_worker(self, paths: list[Path], side: int) -> None:
        for path in paths:
            if not self._alive:
                return
            image: Optional[Image.Image] = None
            meta = None
            try:
                with Image.open(path) as img:
                    real = img.size
                    img.draft("RGB", (side * 3, side * 3))
                    image = img.convert("RGBA")
                    image.thumbnail((side, side), Image.Resampling.LANCZOS)
                    meta = f"{converter.human_size(path.stat().st_size)} · {real[0]}×{real[1]}"
            except Exception:  # noqa: BLE001 - 单张图缩略图失败不影响列表
                image = None
            self._events.put(("thumb", path, image, meta))
        self._events.put(("thumbs_done",))

    def _pump(self) -> None:
        if not self._alive:
            return
        handled = 0
        while handled < 400:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            handled += 1
            kind = event[0]
            if kind == "scan":
                self._scanning = False
                self._found = list(event[1])
                self._start_build()
            elif kind == "thumb":
                _, path, image, meta = event
                row = self._rows.get(path)
                if row is not None:
                    row.set_thumb(image, meta)
            elif kind == "thumbs_done":
                self._thumbs_running = False
        self._update_footer()
        self.after(40, self._pump)

    # ================================================================ 选择 --
    def _query(self) -> str:
        if self._placeholder_on:
            return ""
        return self.search_var.get().strip()

    def _search_key_press(self, event: tk.Event):
        """第一次按键才清掉占位符，让「搜索」这件事一眼看得出来。"""
        if self._placeholder_on and (event.char or event.keysym == "BackSpace"):
            self._placeholder_on = False
            self.search_entry.delete(0, "end")
            self.search_entry.configure(fg=self.theme.color("text_primary"))
        return None

    def _search_focus_in(self, _event: tk.Event) -> None:
        self._search_frame.configure(highlightbackground=self.theme.color("accent"))

    def _search_focus_out(self, _event: tk.Event) -> None:
        self._search_frame.configure(highlightbackground=self.theme.color("border"))
        if not self.search_var.get().strip():
            self._placeholder_on = True
            self.search_entry.delete(0, "end")
            self.search_entry.insert(0, _PLACEHOLDER)
            self.search_entry.configure(fg=self.theme.color("text_secondary"))

    def _search_changed(self, *_args) -> None:
        if self._placeholder_on:
            return
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except tk.TclError:
                pass
        self._search_job = self.after(SEARCH_DELAY, self._apply_filter)

    def _apply_filter(self, keep_scroll: bool = True) -> None:
        self._search_job = None
        query = self._query()
        self._visible = [path for path in self._build_paths if matches(path.name, query)]
        for row in self._rows.values():
            row.pack_forget()
        for path in self._visible:
            row = self._rows.get(path)
            if row is not None:
                row.pack(fill="x")
        if self._build_paths and not self._visible:
            self.empty_label.configure(text="没有匹配的图片")
            self.empty_label.pack(fill="x", pady=self.theme.px(40))
        elif self._visible:
            self.empty_label.pack_forget()
        if not keep_scroll:
            self.scroll.scroll_to_top()
        self._focus_index = None
        self._sync_focus()
        self._update_footer()

    def _set_checked(self, path: Path, value: bool) -> None:
        row = self._rows.get(path)
        if row is None or row.taken:
            return
        row.set_checked(value)
        if value:
            self._checked.add(path)
        else:
            self._checked.discard(path)

    def _toggle(self, row: PickerRow, shift: bool) -> None:
        if row.taken:
            self._flash(f"「{row.path.name}」已经在列表里了")
            return
        if self._query() and row.path in self._visible:
            index = self._visible.index(row.path)
        else:
            index = self._visible.index(row.path) if row.path in self._visible else None
        target = not row.checked
        if shift and self._anchor is not None and index is not None:
            low, high = sorted((self._anchor, index))
            for path in self._visible[low:high + 1]:
                self._set_checked(path, target)
        else:
            self._set_checked(row.path, target)
            self._anchor = index
        self._focus_index = index
        self._focus_path = row.path
        self._sync_focus()
        self._update_footer()

    def _activate(self, row: PickerRow) -> None:
        """双击 = 只处理这一张。"""
        if row.taken:
            self._flash(f"「{row.path.name}」已经在列表里了")
            return
        self._checked = {row.path}
        self._focus_path = row.path
        self._sync_focus()
        self._confirm(False)

    def _select_all(self) -> None:
        for path in self._visible:
            self._set_checked(path, True)
        self._sync_focus()
        self._update_footer()

    def _select_none(self) -> None:
        for path in list(self._checked):
            self._set_checked(path, False)
        self._update_footer()

    # -- 键盘 -----------------------------------------------------------
    def _typing(self) -> bool:
        try:
            return self.focus_get() is self.search_entry
        except tk.TclError:
            return False

    def _key_move(self, event: tk.Event):
        if self._typing() or not self._visible:
            return None
        if self._focus_index is None:
            index = 0 if event.keysym == "Down" else len(self._visible) - 1
        else:
            step = 1 if event.keysym == "Down" else -1
            index = max(0, min(len(self._visible) - 1, self._focus_index + step))
        self._focus_index = index
        self._focus_path = self._visible[index]
        self._anchor = index
        self._sync_focus()
        self._ensure_visible(self._focus_path)
        return "break"

    def _key_toggle(self, _event: tk.Event):
        if self._typing() or self._focus_index is None:
            return None
        path = self._visible[self._focus_index]
        row = self._rows.get(path)
        if row is not None:
            self._toggle(row, False)
        return "break"

    def _key_select_all(self, _event: tk.Event):
        if self._typing():
            return None
        self._select_all()
        return "break"

    def _sync_focus(self) -> None:
        for path, row in self._rows.items():
            desired = path == self._focus_path
            if row.focused != desired:
                row.set_focused(desired)

    def _ensure_visible(self, path: Path) -> None:
        row = self._rows.get(path)
        if row is None:
            return
        try:
            self.update_idletasks()
            canvas = self.scroll.canvas
            top = canvas.canvasy(0)
            view = canvas.winfo_height()
            y = row.winfo_y()
            height = row.winfo_height()
            if y < top or y + height > top + view:
                total = max(1, self.scroll.inner.winfo_height())
                canvas.yview_moveto(max(0.0, min(1.0, (y - view / 3) / total)))
        except tk.TclError:
            pass

    # ================================================================ 底部 --
    def _update_footer(self) -> None:
        total = len(self._build_paths)
        shown = len(self._visible)
        chosen = len(self._checked)
        if self._scanning:
            text = "正在扫描…"
        elif not total:
            text = "没有找到支持的图片"
        else:
            bits = [f"{total} 张图片"]
            if shown != total:
                bits.append(f"筛选后 {shown}")
            bits.append(f"已选 {chosen}")
            text = " · ".join(bits)
            if total > MAX_ROWS:
                text += f"（列表只显示前 {MAX_ROWS} 个）"
        self.count_label.configure(text=text, fg=self.theme.color("text_secondary"))
        self.confirm_btn.set_text(f"添加选中 · {chosen}" if chosen else "添加选中")
        self.confirm_btn.set_enabled(chosen > 0)
        self.start_btn.set_enabled(chosen > 0)

    def _flash(self, text: str) -> None:
        self.count_label.configure(text=text, fg=self.theme.color("warning"))
        self.after(1600, lambda: self._update_footer() if self._alive else None)

    # ================================================================ 动作 --
    def _change_folder(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(parent=self, title="选择包含图片的文件夹", initialdir=str(self.folder))
        if not chosen:
            return
        self.folder = Path(chosen)
        self.folder_label.configure(text=truncate(str(self.folder), 62))
        self._scan()

    def _toggle_recursive(self, value: bool) -> None:
        self.recursive = value
        self._scan()

    def _confirm(self, start: bool) -> None:
        if not self._checked:
            self._flash("先勾选要处理的图片")
            return
        ordered = [path for path in self._build_paths if path in self._checked]
        if not ordered:
            self._flash("先勾选要处理的图片")
            return
        self.result = ordered
        self.start_requested = start
        self._close()

    def _cancel(self) -> None:
        self.result = []
        self.start_requested = False
        self._close()

    def _close(self) -> None:
        self._alive = False
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _apply_titlebar(self) -> None:
        if not self._alive:
            return
        from .gui import apply_title_bar_theme

        apply_title_bar_theme(self, self.theme.mode == "dark")