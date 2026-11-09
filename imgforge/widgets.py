"""自绘控件集合。

tkinter 自带控件在 Windows 上要么是系统灰、要么被 ttk clam 限制，
所以列表行、格式胶囊、多选框这些"有状态"的小件都自己画：
每个状态（常态/悬停/按下/选中/焦点/禁用）都有明确颜色，不靠系统默认。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable, Iterable, Optional

from PIL import Image, ImageTk

from .theme import Theme

# ------------------------------------------------------------------ 基础画法 --


def round_rect_points(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 6) -> list[float]:
    """圆角矩形的多边形顶点（逆时针），用精确圆弧点而不是 smooth 近似。"""
    r = max(0.0, min(r, (x1 - x0) / 2 - 0.5, (y1 - y0) / 2 - 0.5))
    points: list[float] = []
    corners = (
        (x1 - r, y0 + r, -90.0, 0.0),   # 右上
        (x1 - r, y1 - r, 0.0, 90.0),    # 右下
        (x0 + r, y1 - r, 90.0, 180.0),  # 左下
        (x0 + r, y0 + r, 180.0, 270.0), # 左上
    )
    import math

    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            angle = math.radians(a0 + (a1 - a0) * i / steps)
            points.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> int:
    return canvas.create_polygon(round_rect_points(x0, y0, x1, y1, r), **kw)


def dashed_round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> int:
    """虚线圆角：四条直边 + 四段短弧，tk 的 dash 只对线段生效。"""
    import math

    items = []
    r = min(r, (x1 - x0) / 2 - 1, (y1 - y0) / 2 - 1)
    # 直边
    items.append(canvas.create_line(x0 + r, y0, x1 - r, y0, **kw))
    items.append(canvas.create_line(x1, y0 + r, x1, y1 - r, **kw))
    items.append(canvas.create_line(x1 - r, y1, x0 + r, y1, **kw))
    items.append(canvas.create_line(x0, y1 - r, x0, y0 + r, **kw))
    # 圆角
    arcs = ((x1 - 2 * r, y0, x1, y0 + 2 * r, -90, 0),
            (x1 - 2 * r, y1 - 2 * r, x1, y1, 0, 90),
            (x0, y1 - 2 * r, x0 + 2 * r, y1, 90, 180),
            (x0, y0, x0 + 2 * r, y0 + 2 * r, 180, 270))
    for ax0, ay0, ax1, ay1, start, end in arcs:
        items.append(canvas.create_arc(ax0, ay0, ax1, ay1, start=start, extent=end - start, style="arc", **kw))
    return items  # type: ignore[return-value]


def truncate(text: str, limit: int = 46) -> str:
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    keep = limit - 1
    head = (keep + 1) // 2
    return text[:head] + "…" + text[len(text) - (keep - head):]


# ------------------------------------------------------------------- tooltip --


class Tooltip:
    """悬停 450ms 后出现的轻提示；只在指针还在控件上时显示。"""

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str], theme: Theme, delay: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.theme = theme
        self.delay = delay
        self._after: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    # -- 生命周期 -------------------------------------------------------
    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _resolve(self) -> str:
        value = self.text() if callable(self.text) else self.text
        return value or ""

    def _show(self) -> None:
        self._after = None
        message = self._resolve()
        if not message or self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + self.theme.px(14)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + self.theme.px(6)
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=self.theme.color("border_strong"))
        p = self.theme.px(8)
        label = tk.Label(
            tip,
            text=message,
            justify="left",
            bg=self.theme.color("surface_raised"),
            fg=self.theme.color("text_primary"),
            font=self.theme.font("caption"),
            padx=p,
            pady=self.theme.px(5),
        )
        label.pack(padx=1, pady=1)
        tip.update_idletasks()
        width, height = tip.winfo_width(), tip.winfo_height()
        screen_w = tip.winfo_screenwidth()
        if x + width > screen_w - self.theme.px(8):
            x = max(self.theme.px(8), screen_w - width - self.theme.px(8))
        tip.wm_geometry(f"+{int(x)}+{int(y)}")
        self._tip = tip

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# --------------------------------------------------------------- scrollframe --


class ScrollFrame(tk.Frame):
    """纵向滚动容器：滚轮只在指针进入时接管，离开立刻还回去。"""

    def __init__(self, master: tk.Misc, theme: Theme, *, background: str = "surface") -> None:
        super().__init__(master, bg=theme.color(background), highlightthickness=0, bd=0)
        self.theme = theme
        self._bg = background
        self.canvas = tk.Canvas(
            self,
            bg=theme.color(background),
            highlightthickness=0,
            bd=0,
            yscrollincrement=theme.px(24),
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", style="Thin.Vertical.TScrollbar", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._bar_visible = False
        self.inner = tk.Frame(self.canvas, bg=theme.color(background), highlightthickness=0, bd=0)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(sequence, self._on_wheel)
        self.canvas.bind("<Enter>", self._grab_wheel)
        self.canvas.bind("<Leave>", self._release_wheel)
        self.inner.bind("<Enter>", self._grab_wheel)
        self.inner.bind("<Leave>", self._release_wheel)

    # -- 内部 -----------------------------------------------------------
    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        need = not (float(first) <= 0.0 and float(last) >= 1.0)
        if need != self._bar_visible:
            self._bar_visible = need
            if need:
                self.scrollbar.pack(side="right", fill="y")
            else:
                self.scrollbar.pack_forget()

    def _on_inner(self, _event: tk.Event | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _grab_wheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _release_wheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event: tk.Event) -> None:
        if not self._bar_visible:
            return
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = -1 * int(getattr(event, "delta", 0) / 120) * 3
        if delta:
            self.canvas.yview_scroll(delta, "units")

    # -- 对外 -----------------------------------------------------------
    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def scroll_to_bottom(self) -> None:
        self.canvas.yview_moveto(1.0)

    def apply_theme(self) -> None:
        color = self.theme.color(self._bg)
        self.configure(bg=color)
        self.canvas.configure(bg=color)
        self.inner.configure(bg=color)


# -------------------------------------------------------------------- 胶囊 --


class Chip(tk.Canvas):
    """单选胶囊：常态 / 悬停 / 按下 / 选中 / 焦点 / 禁用 六态自绘。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        text: str,
        value: str,
        command: Callable[[str], None],
        *,
        background: str = "surface_raised",
    ) -> None:
        self.theme = theme
        self.value = value
        self.command = command
        self._bg_role = background
        self._selected = False
        self._hover = False
        self._pressed = False
        self._enabled = True
        self._focus = False
        self._chip_height = theme.px(30)
        font = theme.font("body")
        width = font.measure(text) + theme.px(24)
        super().__init__(
            master,
            width=width,
            height=self._chip_height,
            bg=theme.color(background),
            highlightthickness=0,
            bd=0,
            takefocus=1,
        )
        self.text = text
        self._radius = theme.px(7)
        self._shape = round_rect(self, 1, 1, width - 1, self._chip_height - 1, self._radius, fill=theme.color("surface_raised"), outline="")
        self._label = self.create_text(width / 2, self._chip_height / 2, text=text, font=font, fill=theme.color("text_primary"))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", lambda _e: self.invoke())
        self.bind("<Return>", lambda _e: self.invoke())
        self._paint()

    # -- 状态 -----------------------------------------------------------
    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, value: bool) -> None:
        self._selected = bool(value)
        self._paint()

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if not value:
            self._hover = self._pressed = False
        self._paint()

    def invoke(self) -> None:
        if not self._enabled:
            return
        self.command(self.value)

    # -- 事件 -----------------------------------------------------------
    def _on_enter(self, _event: tk.Event) -> None:
        self._hover = True
        self._paint()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._pressed = False
        self._paint()

    def _on_press(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self.focus_set()
        self._paint()

    def _on_release(self, event: tk.Event) -> None:
        was = self._pressed
        self._pressed = False
        self._paint()
        if not was or not self._enabled:
            return
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.invoke()

    def _on_focus_in(self, _event: tk.Event) -> None:
        self._focus = True
        self._paint()

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._focus = False
        self._paint()

    # -- 绘制 -----------------------------------------------------------
    def _paint(self) -> None:
        theme = self.theme
        if not self._enabled:
            fill = theme.color("surface_sunken")
            outline = theme.color("border")
            text = theme.color("text_disabled")
        elif self._selected:
            fill = theme.color("accent_active" if self._pressed else "accent")
            outline = theme.color("accent")
            text = theme.color("on_accent")
        elif self._pressed:
            fill = theme.color("surface_sunken")
            outline = theme.color("border_strong")
            text = theme.color("text_primary")
        elif self._hover:
            fill = theme.color("surface_hover")
            outline = theme.color("border_strong")
            text = theme.color("text_primary")
        else:
            fill = theme.color("surface_raised")
            outline = theme.color("border")
            text = theme.color("text_primary")
        self.itemconfigure(self._shape, fill=fill, outline=outline, width=1)
        self.itemconfigure(self._label, fill=text)
        if self._focus and self._enabled:
            self.itemconfigure(self._shape, outline=theme.color("focus"), width=theme.px(2))
        self.configure(bg=theme.color(self._bg_role))


class ChipGroup(tk.Frame):
    """一组互斥胶囊，支持方向键切换。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        options: Iterable[tuple[str, str]],
        value: str,
        command: Callable[[str], None],
        *,
        background: str = "surface_raised",
        columns: int = 4,
        gap: int | None = None,
    ) -> None:
        super().__init__(master, bg=theme.color(background), highlightthickness=0, bd=0)
        self.theme = theme
        self.command = command
        self._value = value
        self.chips: dict[str, Chip] = {}
        gap = gap if gap is not None else theme.px(6)
        for index, (text, key) in enumerate(options):
            chip = Chip(self, theme, text, key, self._on_chip, background=background)
            chip.grid(row=index // columns, column=index % columns, padx=(0, gap), pady=(0, gap), sticky="w")
            chip.bind("<Left>", lambda _e: self._nudge(-1))
            chip.bind("<Right>", lambda _e: self._nudge(1))
            self.chips[key] = chip
        self.set(value)

    def _on_chip(self, value: str) -> None:
        self.set(value)
        self.command(value)

    def _nudge(self, step: int) -> None:
        keys = list(self.chips)
        if not keys:
            return
        index = (keys.index(self._value) + step) % len(keys)
        self._on_chip(keys[index])
        self.chips[keys[index]].focus_set()

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        if value not in self.chips:
            value = next(iter(self.chips))
        self._value = value
        for key, chip in self.chips.items():
            chip.set_selected(key == value)

    def set_enabled(self, value: bool) -> None:
        for chip in self.chips.values():
            chip.set_enabled(value)


# ------------------------------------------------------------------ 复选框 --


class CheckRow(tk.Frame):
    """自绘勾选框 + 标题 + 说明；整行可点，键盘空格可切。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        label: str,
        value: bool = True,
        command: Optional[Callable[[bool], None]] = None,
        *,
        hint: str = "",
        background: str = "surface_raised",
        compact: bool = False,
    ) -> None:
        self.theme = theme
        self._bg_role = background
        super().__init__(master, bg=theme.color(background), highlightthickness=0, bd=0, takefocus=1)
        self._value = bool(value)
        self.command = command
        self._hover = False
        self._focus = False
        self._enabled = True
        box = theme.px(18)
        pad = theme.px(4)
        self.canvas = tk.Canvas(self, width=box + pad * 2, height=box + pad * 2, bg=theme.color(background), highlightthickness=0, bd=0)
        self.canvas.pack(side="left")
        self._box_size = box
        self._box_pad = pad
        self._box = round_rect(self.canvas, pad, pad, pad + box, pad + box, theme.px(4), fill=theme.color("surface_raised"), outline=theme.color("border_strong"))
        self._tick = self.canvas.create_line(
            pad + box * 0.26, pad + box * 0.52,
            pad + box * 0.44, pad + box * 0.71,
            pad + box * 0.76, pad + box * 0.30,
            fill=theme.color("on_accent"),
            width=max(2, theme.px(2)),
            capstyle="round",
            joinstyle="round",
            state="hidden",
        )
        gap = theme.px(2 if compact else 6)
        text_wrap = theme.px(210)
        self.label = tk.Label(
            self,
            text=label,
            bg=theme.color(background),
            fg=theme.color("text_primary"),
            font=theme.font("caption_bold" if compact else "body"),
            anchor="w",
            justify="left",
        )
        self.label.pack(side="left", padx=(gap, 0))
        self.hint_label: Optional[tk.Label] = None
        if hint and not compact:
            self.hint_label = tk.Label(
                self,
                text=hint,
                bg=theme.color(background),
                fg=theme.color("text_secondary"),
                font=theme.font("caption"),
                anchor="w",
                wraplength=text_wrap,
                justify="left",
            )
            self.hint_label.pack(side="left", padx=(theme.px(6), 0))

        for widget in (self, self.label, self.canvas):
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<ButtonPress-1>", self._on_click, add="+")
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", lambda _e: self.toggle())
        self.bind("<Return>", lambda _e: self.toggle())
        self._paint()

    @property
    def value(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._paint()

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self.configure(takefocus=1 if value else 0)
        self._paint()

    def toggle(self) -> None:
        if not self._enabled:
            return
        self._value = not self._value
        self._paint()
        if self.command:
            self.command(self._value)

    # -- 事件 -----------------------------------------------------------
    def _on_enter(self, _event: tk.Event) -> None:
        self._hover = True
        self._paint()

    def _on_leave(self, event: tk.Event) -> None:
        # 子控件之间移动也会触发 Leave，检查指针是否真的离开了整行
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            under = None
        widget = under
        while widget is not None:
            if widget is self:
                return
            widget = getattr(widget, "master", None)
        self._hover = False
        self._paint()

    def _on_click(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self.toggle()

    def _on_focus_in(self, _event: tk.Event) -> None:
        self._focus = True
        self._paint()

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._focus = False
        self._paint()

    # -- 绘制 -----------------------------------------------------------
    def _paint(self) -> None:
        theme = self.theme
        bg = theme.color(self._bg_role)
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.label.configure(bg=bg, fg=theme.color("text_disabled" if not self._enabled else "text_primary"))
        if self.hint_label is not None:
            self.hint_label.configure(bg=bg, fg=theme.color("text_disabled" if not self._enabled else "text_secondary"))
        if not self._enabled:
            fill, outline = theme.color("surface_sunken"), theme.color("border")
        elif self._value:
            fill = theme.color("accent_hover" if self._hover else "accent")
            outline = theme.color("accent")
        elif self._hover:
            fill, outline = theme.color("surface_hover"), theme.color("accent")
        else:
            fill, outline = theme.color("surface_raised"), theme.color("border_strong")
        width = theme.px(2) if (self._focus and self._enabled) else 1
        if self._focus and self._enabled:
            outline = theme.color("focus")
        self.canvas.itemconfigure(self._box, fill=fill, outline=outline, width=width)
        self.canvas.itemconfigure(self._tick, state="normal" if self._value else "hidden")


# ------------------------------------------------------------- 图标按钮 --


class IconButton(tk.Canvas):
    """画矢量图形的小按钮（主题切换、关闭等），不依赖 emoji 或图片。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        glyph: str,
        command: Callable[[], None],
        *,
        background: str = "surface",
        size: int | None = None,
        tooltip: str = "",
    ) -> None:
        self.theme = theme
        self.glyph = glyph
        self.command = command
        self._bg_role = background
        side = size or theme.px(30)
        super().__init__(master, width=side, height=side, bg=theme.color(background), highlightthickness=0, bd=0, takefocus=1)
        self._side = side
        self._hover = False
        self._pressed = False
        self._focus = False
        self._shape = round_rect(self, 1, 1, side - 1, side - 1, theme.px(6), fill=theme.color(background), outline="")
        self._parts: list[int] = []
        self._cutout: int | None = None
        self._paint_glyph()
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<space>", lambda _e: self.command())
        self.bind("<Return>", lambda _e: self.command())
        if tooltip:
            self.tooltip = Tooltip(self, tooltip, theme)  # noqa: F841 - 由 self 持有
        self._paint()

    # -- 状态 -----------------------------------------------------------
    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._paint()

    def _set_focus(self, value: bool) -> None:
        self._focus = value
        self._paint()

    def _on_press(self, _event: tk.Event) -> None:
        self._pressed = True
        self.focus_set()
        self._paint()

    def _on_release(self, event: tk.Event) -> None:
        was = self._pressed
        self._pressed = False
        self._paint()
        if was and 0 <= event.x <= self._side and 0 <= event.y <= self._side:
            self.command()

    # -- 图形 -----------------------------------------------------------
    def _paint_glyph(self) -> None:
        self.delete("glyph")
        self._cutout = None
        theme, side = self.theme, self._side
        c = side / 2
        stroke = max(1.5, theme.px(1.6))
        color = theme.color("text_primary")
        if self.glyph == "sun":
            r = side * 0.19
            self.create_oval(c - r, c - r, c + r, c + r, outline=color, width=stroke, tags="glyph")
            import math

            for i in range(8):
                angle = math.radians(i * 45)
                inner = side * 0.29
                outer = side * 0.37
                self.create_line(
                    c + inner * math.cos(angle), c + inner * math.sin(angle),
                    c + outer * math.cos(angle), c + outer * math.sin(angle),
                    fill=color, width=stroke, capstyle="round", tags="glyph",
                )
        elif self.glyph == "moon":
            # 实心圆 + 同底色圆切出弯月，比纯描边在 32px 下更清楚
            r = side * 0.30
            self.create_oval(c - r, c - r, c + r, c + r, fill=color, outline="", tags="glyph")
            cut_r = r
            cut_x, cut_y = c + r * 0.50, c - r * 0.38
            self._cutout = self.create_oval(
                cut_x - cut_r, cut_y - cut_r, cut_x + cut_r, cut_y + cut_r,
                fill=self._fill_color(), outline="", tags="glyph",
            )
        elif self.glyph == "close":
            pad = side * 0.3
            self.create_line(pad, pad, side - pad, side - pad, fill=color, width=stroke, capstyle="round", tags="glyph")
            self.create_line(side - pad, pad, pad, side - pad, fill=color, width=stroke, capstyle="round", tags="glyph")
        elif self.glyph == "folder":
            w, h = side * 0.52, side * 0.38
            x0, y0 = c - w / 2, c - h / 2 + side * 0.03
            self.create_rectangle(x0, y0, x0 + w, y0 + h, outline=color, width=stroke, tags="glyph")
            self.create_line(x0 + side * 0.04, y0 - side * 0.08, x0 + w * 0.5, y0 - side * 0.08, fill=color, width=stroke, tags="glyph")
        elif self.glyph == "trash":
            self.create_rectangle(c - side * 0.13, c - side * 0.1, c + side * 0.13, c + side * 0.2, outline=color, width=stroke, tags="glyph")
            self.create_line(c - side * 0.2, c - side * 0.14, c + side * 0.2, c - side * 0.14, fill=color, width=stroke, tags="glyph")
            self.create_line(c - side * 0.06, c - side * 0.19, c + side * 0.06, c - side * 0.19, fill=color, width=stroke, tags="glyph")
        self._parts = self.find_withtag("glyph")

    def _fill_color(self) -> str:
        theme = self.theme
        if self._pressed:
            return theme.color("surface_sunken")
        if self._hover:
            return theme.color("surface_hover")
        return theme.color(self._bg_role)

    def _paint(self) -> None:
        theme = self.theme
        fill = self._fill_color()
        self.itemconfigure(self._shape, fill=fill, outline=theme.color("focus") if self._focus else fill)
        cutout = getattr(self, "_cutout", None)
        if cutout is not None:
            self.itemconfigure(cutout, fill=fill)
        self.configure(bg=theme.color(self._bg_role))

    # 主题切换后重画
    def refresh(self) -> None:
        self._paint_glyph()
        self._paint()


class TextButton(tk.Canvas):
    """自绘文字按钮：tk.Button 的 flat 样式在 Windows 上不画 highlight 边框，
    白底按钮放在白底面板里会"消失"，所以这里自己画底和描边。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        text: str,
        command: Callable[[], None],
        *,
        variant: str = "secondary",   # primary（强调色填充）/ secondary（白底描边）/ ghost（纯文字）
        background: str = "surface_raised",
        min_width: int = 0,
    ) -> None:
        self.theme = theme
        self.command = command
        self.variant = variant
        self._bg_role = background
        self._text = text
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focus = False
        self._font = theme.font("body_bold" if variant == "primary" else "body")
        px = theme.px
        height = px(36)
        pad = px(20 if variant == "primary" else 14)
        measured = tkfont.Font(root=master, font=self._font).measure(text)
        width = max(min_width, measured + pad * 2)
        super().__init__(
            master, width=width, height=height, bg=theme.color(background),
            highlightthickness=0, bd=0, takefocus=1, cursor="hand2",
        )
        self._shape = round_rect(self, 1, 1, width - 1, height - 1, px(6), outline="")
        self._label = self.create_text(width / 2, height / 2, text=text, font=self._font)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<space>", lambda _e: self._invoke())
        self.bind("<Return>", lambda _e: self._invoke())
        self._paint()

    # -- 状态 -----------------------------------------------------------
    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._paint()

    def _set_focus(self, value: bool) -> None:
        self._focus = value
        self._paint()

    def _on_press(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self.focus_set()
        self._paint()

    def _on_release(self, event: tk.Event) -> None:
        was = self._pressed
        self._pressed = False
        self._paint()
        if was and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self._invoke()

    def _invoke(self) -> None:
        if self._enabled:
            self.command()

    def set_enabled(self, value: bool) -> None:
        self._enabled = value
        self.configure(cursor="hand2" if value else "arrow")
        self._paint()

    def set_text(self, text: str) -> None:
        """只换文字，不改宽度（宽度按创建时的文字定，需要时用 min_width 预留）。"""
        if text == self._text:
            return
        self._text = text
        self.itemconfigure(self._label, text=text)

    # -- 配色 -----------------------------------------------------------
    def _colors(self) -> tuple[str, str, str]:
        theme = self.theme
        if not self._enabled:
            return theme.color("surface_sunken"), theme.color("text_disabled"), theme.color("border")
        if self.variant == "primary":
            if self._pressed:
                fill = theme.color("accent_active")
            elif self._hover:
                fill = theme.color("accent_hover")
            else:
                fill = theme.color("accent")
            return fill, theme.color("on_accent"), fill
        if self.variant == "ghost":
            fill = theme.color("surface_hover") if (self._hover or self._pressed) else theme.color(self._bg_role)
            return fill, theme.color("text_secondary"), fill
        if self._pressed:
            fill = theme.color("surface_sunken")
        elif self._hover:
            fill = theme.color("surface_hover")
        else:
            fill = theme.color("surface_raised")
        return fill, theme.color("text_primary"), theme.color("border_strong")

    def _paint(self) -> None:
        fill, fg, border = self._colors()
        outline = self.theme.color("focus") if self._focus else border
        width = max(1, int(self.theme.px(1.5)))
        self.itemconfigure(self._shape, fill=fill, outline=outline, width=width if self._focus else 1)
        self.itemconfigure(self._label, fill=fg)
        self.configure(bg=self.theme.color(self._bg_role))


# ------------------------------------------------------------------ 空状态 --


class DropZone(tk.Canvas):
    """空列表时的放置区：虚线圆角框 + 主副标题。"""

    def __init__(self, master: tk.Misc, theme: Theme, *, title: str, subtitle: str, hint: str = "") -> None:
        super().__init__(master, bg=theme.color("surface_raised"), highlightthickness=0, bd=0)
        self.theme = theme
        self._title = title
        self._subtitle = subtitle
        self._hint = hint
        self._active = False
        self.bind("<Configure>", lambda _e: self._draw())

    def set_active(self, value: bool) -> None:
        if value != self._active:
            self._active = value
            self._draw()

    def _draw(self) -> None:
        theme = self.theme
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 60 or height < 60:
            return
        inset = theme.px(10)
        color = theme.color("accent") if self._active else theme.color("border_strong")
        width_line = theme.px(2) if self._active else 1
        dash = (theme.px(6), theme.px(5))
        dashed_round_rect(self, inset, inset, width - inset, height - inset, theme.px(10), fill=color, width=width_line, dash=dash)
        center_y = height / 2
        icon_r = theme.px(18)
        icon_color = theme.color("accent" if self._active else "text_secondary")
        self.create_rectangle(
            width / 2 - icon_r, center_y - icon_r - theme.px(26), width / 2 + icon_r, center_y - theme.px(26) + icon_r,
            outline=icon_color, width=max(1, theme.px(1.5)),
        )
        self.create_line(
            width / 2 - icon_r * 0.8, center_y - theme.px(26) + icon_r * 0.55,
            width / 2 - icon_r * 0.1, center_y - theme.px(26) - icon_r * 0.1,
            width / 2 + icon_r * 0.35, center_y - theme.px(26) + icon_r * 0.3,
            width / 2 + icon_r * 0.8, center_y - theme.px(26) - icon_r * 0.25,
            fill=icon_color, width=max(1, theme.px(1.5)), smooth=True,
        )
        self.create_text(
            width / 2, center_y + theme.px(16), text=self._title,
            fill=theme.color("text_primary"), font=theme.font("h2"), anchor="center",
        )
        self.create_text(
            width / 2, center_y + theme.px(40), text=self._subtitle,
            fill=theme.color("text_secondary"), font=theme.font("caption"), anchor="center",
        )
        if self._hint:
            self.create_text(
                width / 2, height - theme.px(26), text=self._hint,
                fill=theme.color("text_secondary"), font=theme.font("caption"), anchor="center",
            )


# ------------------------------------------------------------------ 队列行 --


STATUS_COLORS = {
    "idle": "text_secondary",
    "reading": "text_secondary",
    "running": "accent_text",
    "done": "success",
    "failed": "danger",
    "skipped": "warning",
}


class QueueRow(tk.Frame):
    """队列里的一行：选中条 + 缩略图 + 文件名/规格 + 状态 + 移除按钮。"""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        *,
        name: str,
        path_text: str,
        on_select: Callable[["QueueRow"], None],
        on_open: Callable[["QueueRow"], None],
        on_remove: Callable[["QueueRow"], None],
        on_menu: Callable[["QueueRow", tk.Event], None],
    ) -> None:
        self.theme = theme
        self.name = name
        self._on_select = on_select
        self._on_open = on_open
        self._on_remove = on_remove
        self._rows = 1
        self._hover = False
        self._selected = False
        self._status_kind = "idle"
        self._thumb_image: Optional[ImageTk.PhotoImage] = None
        self._thumb_source: Optional[Image.Image] = None
        self._removing = False
        height = theme.px(56)
        super().__init__(master, bg=theme.color("surface_raised"), highlightthickness=0, bd=0, height=height)
        self.pack_propagate(False)
        self._marker = tk.Canvas(self, width=theme.px(3), bg=theme.color("surface_raised"), highlightthickness=0, bd=0)
        self._marker.pack(side="left", fill="y")
        self._marker_line = self._marker.create_rectangle(0, 0, theme.px(3), height, fill=theme.color("accent"), outline="")

        body = tk.Frame(self, bg=theme.color("surface_raised"))
        body.pack(side="left", fill="both", expand=True)
        self._body = body

        thumb_side = theme.px(38)
        self.thumb = tk.Canvas(body, width=thumb_side, height=thumb_side, bg=theme.color("surface_raised"), highlightthickness=0, bd=0)
        self.thumb.pack(side="left", padx=(theme.px(6), theme.px(10)), pady=theme.px(9))
        self._thumb_side = thumb_side
        self._thumb_box = round_rect(self.thumb, 0, 0, thumb_side, thumb_side, theme.px(5), fill=theme.color("surface_sunken"), outline=theme.color("border"))

        texts = tk.Frame(body, bg=theme.color("surface_raised"))
        texts.pack(side="left", fill="both", expand=True, pady=theme.px(9))
        self._texts = texts
        self.name_label = tk.Label(texts, text=name, bg=theme.color("surface_raised"), fg=theme.color("text_primary"),
                                   font=theme.font("body"), anchor="w")
        self.name_label.pack(fill="x")
        self.meta_label = tk.Label(texts, text=path_text, bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
                                   font=theme.font("caption"), anchor="w")
        self.meta_label.pack(fill="x", pady=(theme.px(1), 0))

        self.status_label = tk.Label(body, text="", bg=theme.color("surface_raised"), fg=theme.color("text_secondary"),
                                     font=theme.font("caption_bold"), anchor="e")
        self.status_label.pack(side="right", padx=(theme.px(6), theme.px(4)))

        self.remove_btn = IconButton(body, theme, "close", lambda: self._on_remove(self), background="surface_raised", size=theme.px(26), tooltip="从队列移除 (Del)")
        # 初始不显示，悬停/选中时显示
        self._menu_widgets = (self, body, texts, self.name_label, self.meta_label, self.thumb, self.status_label, self._marker)

        for widget in self._menu_widgets:
            widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<Button-1>", self._on_click, add="+")
            widget.bind("<Double-Button-1>", lambda _e: self._on_open(self), add="+")
            widget.bind("<Button-3>", lambda e: self._on_menu(self, e), add="+")
        self.bind("<Configure>", self._fit_marker)

    def _fit_marker(self, _event: tk.Event) -> None:
        height = self.winfo_height()
        self._marker.coords(self._marker_line, 0, 0, self.theme.px(3), height)

    # -- 状态 -----------------------------------------------------------
    def _set_hover(self, value: bool) -> None:
        if self._hover == value:
            return
        self._hover = value
        self._paint()

    def _on_leave(self, _event: tk.Event) -> None:
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            under = None
        widget = under
        while widget is not None:
            if widget is self:
                return
            widget = getattr(widget, "master", None)
        self._hover = False
        self._paint()

    def _on_click(self, _event: tk.Event) -> None:
        self._on_select(self)

    def set_selected(self, value: bool) -> None:
        if self._selected == value:
            return
        self._selected = value
        self._paint()

    @property
    def selected(self) -> bool:
        return self._selected

    def set_thumb(self, image: Image.Image | None) -> None:
        theme = self.theme
        self.thumb.delete("thumb")
        self._thumb_source = image
        if image is None:
            return
        side = self._thumb_side
        tile = theme.px(5)
        canvas_img = Image.new("RGBA", (side * 2, side * 2), (0, 0, 0, 0))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(canvas_img)
        for y in range(0, side * 2, tile):
            for x in range(0, side * 2, tile):
                shade = theme.color("checker_a") if ((x // tile) + (y // tile)) % 2 == 0 else theme.color("checker_b")
                draw.rectangle([x, y, x + tile, y + tile], fill=shade)
        thumb = image.convert("RGBA")
        thumb.thumbnail((side * 2 - theme.px(4), side * 2 - theme.px(4)), Image.Resampling.LANCZOS)
        canvas_img.alpha_composite(thumb, ((side * 2 - thumb.width) // 2, (side * 2 - thumb.height) // 2))
        canvas_img = canvas_img.resize((side, side), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(canvas_img, master=self.thumb)
        self._thumb_image = photo
        self.thumb.create_image(0, 0, image=photo, anchor="nw", tags="thumb")
        self.thumb.tag_raise(self._thumb_box)
        self.thumb.itemconfigure(self._thumb_box, fill="")

    def set_meta(self, text: str) -> None:
        self.meta_label.configure(text=truncate(text, 64))

    def set_name(self, text: str) -> None:
        self.name_label.configure(text=truncate(text, 52))

    def set_status(self, kind: str, text: str, tooltip_text: str | None = None) -> None:
        self._status_kind = kind
        self._status_tooltip_text = tooltip_text or ""
        if tooltip_text and not hasattr(self, "_status_tip"):
            self._status_tip = Tooltip(self.status_label, lambda: self._status_tooltip_text, self.theme)
        self.status_label.configure(text=truncate(text, 16), fg=self.theme.color(STATUS_COLORS.get(kind, "text_secondary")))

    # -- 绘制 -----------------------------------------------------------
    def _paint(self) -> None:
        theme = self.theme
        if self._selected:
            bg_role = "accent_subtle"
        elif self._hover:
            bg_role = "surface_hover"
        else:
            bg_role = "surface_raised"
        bg = theme.color(bg_role)
        self.configure(bg=bg)
        for widget in (self._body, self._texts, self.name_label, self.meta_label, self.thumb, self.status_label, self.remove_btn, self._marker):
            try:
                widget.configure(bg=bg)
            except tk.TclError:
                pass
        self._marker.itemconfigure(self._marker_line, fill=theme.color("accent") if self._selected else bg)
        self.name_label.configure(fg=theme.color("text_primary"))
        self.meta_label.configure(fg=theme.color("text_secondary"))
        self.status_label.configure(fg=theme.color(STATUS_COLORS.get(self._status_kind, "text_secondary")))
        self.remove_btn._bg_role = bg_role
        self.remove_btn._paint()
        if self._hover or self._selected:
            if not self.remove_btn.winfo_ismapped():
                self.remove_btn.pack(side="right", padx=(0, theme.px(6)))
        else:
            if self.remove_btn.winfo_ismapped():
                self.remove_btn.pack_forget()