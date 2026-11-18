"""Qt 基础控件：卡片、胶囊、分段选择、开关、滑块、拖放区、进度条、Toast。

全部自绘的部分只做两件事：把 token 画出来、把八个状态画全
（default / hover / pressed / focus / disabled / checked / drag / empty）。
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .theme import Theme


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class ThemeAware(QWidget):
    """自动跟随主题变化重绘的基类。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        theme.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        self.update_theme()
        self.update()

    def update_theme(self) -> None:
        pass


# ------------------------------------------------------------------ 卡片 --
class Card(QFrame):
    def __init__(
        self,
        theme: Theme,
        parent: QWidget | None = None,
        *,
        padding: int = 16,
        spacing: int = 12,
        shadow: bool = True,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(28)
            effect.setOffset(0, 6)
            effect.setColor(QColor(23, 32, 45, 26 if not theme.is_dark() else 0))
            self.setGraphicsEffect(effect)
        theme.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsDropShadowEffect):
            effect.setColor(QColor(0, 0, 0, 0 if self.theme.is_dark() else 26))
        repolish(self)


class SectionHeader(QWidget):
    """小节标题：主标题 + 右侧可选说明。"""

    def __init__(self, theme: Theme, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label = QLabel(title)
        self.label.setFont(theme.font("body_bold"))
        self.hint = QLabel("")
        self.hint.setObjectName("Caption")
        row.addWidget(self.label)
        row.addStretch(1)
        row.addWidget(self.hint)

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)


# ---------------------------------------------------------------- 胶囊 --
class Chip(QAbstractButton):
    """可选胶囊：选中时实心强调色，未选中时描边。"""

    def __init__(
        self,
        theme: Theme,
        text: str,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
        min_width: int = 0,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.compact = compact
        self.min_width = min_width
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(30 if compact else 34)
        self._hover = False
        theme.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.theme.font("body" if not self.compact else "caption_medium"))
        width = metrics.horizontalAdvance(self.text()) + (24 if self.compact else 32)
        return QSize(max(width, self.min_width), self.height())

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2 if not self.compact else self.theme.radius("sm")
        checked = self.isChecked()
        enabled = self.isEnabled()
        pressed = self.isDown()
        if not enabled:
            bg = self.theme.qcolor("surface_sunken")
            border = self.theme.qcolor("border")
            fg = self.theme.qcolor("text_disabled")
        elif checked:
            bg = self.theme.qcolor("accent_active" if pressed else ("accent_hover" if self._hover else "accent"))
            border = bg
            fg = self.theme.qcolor("on_accent")
        else:
            bg = self.theme.qcolor("surface_hover" if (self._hover or pressed) else "surface")
            border = self.theme.qcolor("border_strong" if (self._hover or pressed) else "border")
            fg = self.theme.qcolor("text_primary")
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus():
            ring = QRectF(rect).adjusted(-2, -2, 2, 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.theme.qcolor("focus"), 1.4))
            painter.drawRoundedRect(ring, radius + 2, radius + 2)
        painter.setPen(fg)
        painter.setFont(self.theme.font("caption_medium" if self.compact else "body_medium"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class ChipGroup(QWidget):
    valueChanged = Signal(str)

    def __init__(
        self,
        theme: Theme,
        options: Sequence[tuple[str, str]],
        columns: int = 4,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self._columns = columns
        self._compact = compact
        self._buttons: dict[str, Chip] = {}
        self._value = ""
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        from PySide6.QtWidgets import QGridLayout

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        for index, (key, label) in enumerate(options):
            chip = Chip(theme, label, compact=compact)
            chip.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
            self._group.addButton(chip)
            self._buttons[key] = chip
            self._grid.addWidget(chip, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

    def _on_clicked(self, key: str) -> None:
        if key == self._value:
            self._buttons[key].setChecked(True)
            return
        self._value = key
        self.valueChanged.emit(key)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, *, emit: bool = False) -> None:
        if value not in self._buttons:
            return
        changed = value != self._value
        self._value = value
        self._buttons[value].setChecked(True)
        if changed and emit:
            self.valueChanged.emit(value)

    def set_enabled(self, enabled: bool) -> None:
        for chip in self._buttons.values():
            chip.setEnabled(enabled)

    def buttons(self) -> dict[str, Chip]:
        return dict(self._buttons)

    def set_options(self, options: Sequence[tuple[str, str]], *, keep_value: bool = True) -> None:
        """重建选项（模型/降噪档位这类会随选择变化的组）。"""
        previous = self._value if keep_value else ""
        for chip in list(self._buttons.values()):
            self._group.removeButton(chip)
            self._grid.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._buttons.clear()
        self._value = ""
        for index, (key, label) in enumerate(options):
            chip = Chip(self.theme, label, compact=self._compact)
            chip.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
            self._group.addButton(chip)
            self._buttons[key] = chip
            self._grid.addWidget(chip, index // self._columns, index % self._columns)
        if previous in self._buttons:
            self.set_value(previous)


class ToggleChip(QAbstractButton):
    """尺寸档位这类"多选小方块"。"""

    def __init__(self, theme: Theme, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self._hover = False
        theme.changed.connect(lambda: self.update())

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.theme.font("caption_medium"))
        return QSize(max(44, metrics.horizontalAdvance(self.text()) + 26), 30)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.theme.radius("sm")
        checked = self.isChecked()
        enabled = self.isEnabled()
        if not enabled:
            bg = self.theme.qcolor("surface_sunken")
            border = self.theme.qcolor("border")
            fg = self.theme.qcolor("text_disabled")
        elif checked:
            bg = self.theme.qcolor("accent_subtle_hover" if self._hover else "accent_subtle")
            border = self.theme.qcolor("accent")
            fg = self.theme.qcolor("accent_text")
        else:
            bg = self.theme.qcolor("surface_hover" if self._hover else "surface")
            border = self.theme.qcolor("border_strong" if self._hover else "border")
            fg = self.theme.qcolor("text_secondary")
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.theme.qcolor("focus"), 1.4))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)
        painter.setPen(fg)
        painter.setFont(self.theme.font("caption_medium"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class ToggleGrid(QWidget):
    changed = Signal(list)

    def __init__(self, theme: Theme, values: Sequence[int], columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        from PySide6.QtWidgets import QGridLayout

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._buttons: dict[int, ToggleChip] = {}
        self._values = list(values)
        for index, value in enumerate(values):
            chip = ToggleChip(theme, str(value))
            chip.clicked.connect(self._emit)
            self._buttons[value] = chip
            grid.addWidget(chip, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def _emit(self) -> None:
        self.changed.emit(self.value())

    def value(self) -> list[int]:
        return sorted(v for v, chip in self._buttons.items() if chip.isChecked())

    def set_value(self, values: Iterable[int], *, emit: bool = False) -> None:
        wanted = set(values)
        for value, chip in self._buttons.items():
            chip.setChecked(value in wanted)
        if emit:
            self.changed.emit(self.value())

    def set_enabled(self, enabled: bool) -> None:
        for chip in self._buttons.values():
            chip.setEnabled(enabled)


# ---------------------------------------------------------------- 分段控件 --
class SegmentedControl(ThemeAware):
    valueChanged = Signal(str)

    def __init__(
        self,
        theme: Theme,
        options: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
        *,
        height: int = 34,
        icons_by_key: dict[str, str] | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self._options = list(options)
        self._icons = icons_by_key or {}
        self._value = self._options[0][0] if self._options else ""
        self._hover_index = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._indicator = 0.0
        self._anim = QPropertyAnimation(self, b"indicator", self)
        self._anim.setDuration(theme.duration("fast"))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if self._options:
            self._indicator = 0.0

    # 指示条位置（0..len-1，可插值）
    def get_indicator(self) -> float:
        return self._indicator

    def set_indicator(self, value: float) -> None:
        self._indicator = value
        self.update()

    # Qt 动画读取的属性（必须用 Property 注册，QPropertyAnimation 才认识）
    indicator = Property(float, get_indicator, set_indicator)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, *, emit: bool = False) -> None:
        keys = [key for key, _ in self._options]
        if value not in keys:
            return
        changed = value != self._value
        self._value = value
        self._animate_to(keys.index(value))
        if changed and emit:
            self.valueChanged.emit(value)

    def _animate_to(self, index: int) -> None:
        if self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(float(self._indicator))
        self._anim.setEndValue(float(index))
        self._anim.start()

    def _index_at(self, x: float) -> int:
        if not self._options:
            return -1
        width = (self.width() - 8) / len(self._options)
        index = int((x - 4) // width)
        return max(0, min(len(self._options) - 1, index))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position().x())
        if 0 <= index < len(self._options):
            key = self._options[index][0]
            if key != self._value:
                self._value = key
                self._animate_to(index)
                self.valueChanged.emit(key)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._index_at(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        keys = [key for key, _ in self._options]
        if not keys:
            return
        index = keys.index(self._value) if self._value in keys else 0
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            index = (index - 1) % len(keys)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            index = (index + 1) % len(keys)
        else:
            super().keyPressEvent(event)
            return
        if keys[index] != self._value:
            self._value = keys[index]
            self._animate_to(index)
            self.valueChanged.emit(self._value)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._options:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = self.theme.radius("md")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.theme.qcolor("surface_sunken"))
        painter.drawRoundedRect(rect, radius, radius)

        count = len(self._options)
        segment = (rect.width() - 8) / count
        indicator_rect = QRectF(4 + self._indicator * segment, 4, segment, rect.height() - 8)
        painter.setBrush(self.theme.qcolor("surface"))
        painter.setPen(QPen(self.theme.qcolor("border"), 1))
        painter.drawRoundedRect(indicator_rect, radius - 4, radius - 4)

        for index, (key, label) in enumerate(self._options):
            cell = QRectF(4 + index * segment, 4, segment, rect.height() - 8)
            selected = index == round(self._indicator)
            color = self.theme.qcolor("text_primary" if selected else "text_secondary")
            if index == self._hover_index and not selected:
                color = self.theme.qcolor("text_primary")
            painter.setPen(color)
            painter.setFont(self.theme.font("caption_medium" if len(label) <= 2 else "body_medium"))
            icon_name = self._icons.get(key)
            if icon_name:
                icon_size = 16
                text_width = QFontMetrics(painter.font()).horizontalAdvance(label)
                total = text_width + icon_size + 6
                start = cell.center().x() - total / 2
                icon_rect = QRectF(start, cell.center().y() - icon_size / 2, icon_size, icon_size)
                icons.draw(painter, icon_name, icon_rect, color, 1.8)
                text_rect = QRectF(start + icon_size + 6, cell.top(), text_width + 4, cell.height())
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            else:
                painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, label)

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.theme.qcolor("focus"), 1.4))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        # 键盘 focus 反馈：当前段下方短横线
        if self.hasFocus():
            cell = QRectF(4 + round(self._indicator) * segment, rect.height() - 4, segment, 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.theme.qcolor("accent"))
            painter.drawRoundedRect(cell.adjusted(segment * 0.3, 0, -segment * 0.3, 0), 1, 1)


# ------------------------------------------------------------------ 开关 --
class SelectBox(QComboBox):
    """下拉选择框：原生弹出列表 + 自绘箭头，和胶囊/分段控件同一套 token。

    选项目的是「选项少但文案长」的场景（例如模型选择），
    比一排胶囊省高度，也比原生 QComboBox 更贴主题。
    """

    valueChanged = Signal(str)

    def __init__(
        self,
        theme: Theme,
        options: Iterable[tuple[str, str]] = (),
        parent: QWidget | None = None,
        *,
        height: int = 36,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self._options: list[tuple[str, str]] = []
        self.setObjectName("SelectBox")
        self.setFont(theme.font("body"))
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_options(options)
        self.currentIndexChanged.connect(self._on_index_changed)
        theme.changed.connect(self.update)

    # ------------------------------------------------------------- 数据 --
    def set_options(self, options: Iterable[tuple[str, str]], *, keep_value: bool = True) -> None:
        current = self.value() if keep_value else ""
        previous = self._options
        self._options = [(str(key), str(label)) for key, label in options]
        blocked = self.blockSignals(True)
        self.clear()
        for key, label in self._options:
            self.addItem(label, key)
        self.blockSignals(blocked)
        keys = [key for key, _ in self._options]
        if current and current in keys:
            self.set_value(current, emit=False)
        elif previous and any(key == previous[0][0] for key in keys):
            self.set_value(previous[0][0], emit=False)
        elif self._options:
            self.setCurrentIndex(0)

    def value(self) -> str:
        data = self.currentData()
        if isinstance(data, dict):
            return str(data.get("key", ""))
        return str(data) if data is not None else ""

    def set_value(self, value: str, *, emit: bool = False) -> None:
        target = str(value)
        for index, (key, _label) in enumerate(self._options):
            if key == target:
                blocked = self.signalsBlocked()
                self.blockSignals(True)
                self.setCurrentIndex(index)
                self.blockSignals(blocked)
                if emit and blocked is False:
                    self.valueChanged.emit(target)
                return

    def _on_index_changed(self, _index: int) -> None:
        if self.signalsBlocked() or not self._options:
            return
        self.valueChanged.emit(self.value())

    # ------------------------------------------------------------- 绘制 --
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = 16
        dpr = float(self.devicePixelRatioF() or 1.0)
        icon = self.theme.icon("chevron_down", "text_disabled" if self.isEnabled() else "text_disabled", size)
        pixmap = icon.pixmap(QSize(size, size), dpr)
        x = self.width() - size - 12
        y = (self.height() - size) // 2
        painter.drawPixmap(x, y, pixmap)
        painter.end()


# ------------------------------------------------------- 彩色短标签 --
#: 标签角色 -> (背景 token, 文字 token)
PILL_ROLES: dict[str, tuple[str, str]] = {
    "accent": ("accent_subtle", "accent_text"),
    "success": ("success_subtle", "success"),
    "warn": ("warning_subtle", "warning"),
    "muted": ("surface_sunken", "text_secondary"),
}
_PILL_HEIGHT = 20
_PILL_PAD = 8
_PILL_GAP = 6


def _pill_font(theme: Theme) -> QFont:
    return theme.font("caption_medium")


def _pills_width(theme: Theme, pills: Sequence[tuple[str, str]]) -> int:
    if not pills:
        return 0
    fm = QFontMetrics(_pill_font(theme))
    total = sum(fm.horizontalAdvance(text) + theme.px(_PILL_PAD * 2) for text, _role in pills)
    return total + theme.px(_PILL_GAP) * (len(pills) - 1)


def _draw_pills(
    painter: QPainter,
    theme: Theme,
    pills: Sequence[tuple[str, str]],
    x: float,
    y_center: float,
    *,
    anchor_right: bool = False,
    dim: bool = False,
) -> None:
    if not pills:
        return
    font = _pill_font(theme)
    fm = QFontMetrics(font)
    widths = [fm.horizontalAdvance(text) + theme.px(_PILL_PAD * 2) for text, _role in pills]
    total = sum(widths) + theme.px(_PILL_GAP) * (len(pills) - 1)
    cursor = x - total if anchor_right else x
    height = theme.px(_PILL_HEIGHT)
    painter.save()
    for (text, role), width in zip(pills, widths):
        bg_role, fg_role = PILL_ROLES.get(role, PILL_ROLES["muted"])
        bg = theme.qcolor(bg_role)
        fg = theme.qcolor(fg_role)
        if dim:
            bg.setAlpha(120)
            fg.setAlpha(140)
        rect = QRectF(cursor, y_center - height / 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setFont(font)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        cursor += width + theme.px(_PILL_GAP)
    painter.restore()


class _PillDelegate(QStyledItemDelegate):
    """弹出列表里的行：名称 + 副标题 + 右侧彩色短标签。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        height = self.theme.px(54) if data.get("desc") else self.theme.px(42)
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        theme = self.theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = QRectF(option.rect)
        painter.fillRect(option.rect, theme.qcolor("surface_raised"))
        inner = outer.adjusted(theme.px(4), theme.px(2), -theme.px(4), -theme.px(2))
        fill = theme.qcolor("surface_raised")
        if option.state & QStyle.StateFlag.State_Selected:
            fill = theme.qcolor("accent_subtle")
        elif option.state & QStyle.StateFlag.State_MouseOver:
            fill = theme.qcolor("surface_hover")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(inner, theme.radius("md"), theme.radius("md"))

        left = inner.left() + theme.px(12)
        right = inner.right() - theme.px(10)
        label_font = theme.font("body_bold")
        painter.setFont(label_font)
        painter.setPen(theme.qcolor("text_primary"))
        fm = QFontMetrics(label_font)
        pills_w = _pills_width(theme, data.get("pills") or ())
        text_w = int(right - left - pills_w - theme.px(10))
        label_rect = QRectF(left, inner.top() + (theme.px(8) if data.get("desc") else 0), text_w, fm.height())
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fm.elidedText(data.get("label", ""), Qt.TextElideMode.ElideRight, max(10, int(text_w))))

        if data.get("desc"):
            desc_font = theme.font("caption")
            painter.setFont(desc_font)
            painter.setPen(theme.qcolor("text_secondary"))
            dfm = QFontMetrics(desc_font)
            painter.drawText(
                QRectF(left, label_rect.bottom() + theme.px(2), right - left, dfm.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                dfm.elidedText(data["desc"], Qt.TextElideMode.ElideRight, max(10, int(right - left))),
            )

        y_center = inner.center().y()
        if data.get("desc"):
            y_center = label_rect.center().y()
        _draw_pills(painter, theme, data.get("pills") or (), right, y_center, anchor_right=True)
        painter.restore()


class PillOptionBox(QComboBox):
    """下拉框：每项 = 名称 +（弹出时）说明 + 右侧彩色短标签胶囊。

    用于「模型」这类选项少、但每个选项都带取舍信息的场景：
    名称给出选择，彩色标签（画质 / 均衡 / 最快 / 动漫）一眼说明怎么挑。
    """

    valueChanged = Signal(str)

    def __init__(
        self,
        theme: Theme,
        options: Iterable[dict] = (),
        parent: QWidget | None = None,
        *,
        height: int = 38,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self._options: list[dict] = []
        self._hover = False
        self.setObjectName("PillSelectBox")
        self.setFont(theme.font("body"))
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setItemDelegate(_PillDelegate(theme, self))
        self.set_options(options)
        self.currentIndexChanged.connect(self._on_index_changed)
        theme.changed.connect(self.update)

    # ------------------------------------------------------------- 数据 --
    def set_options(self, options: Iterable[dict], *, keep_value: bool = True) -> None:
        current = self.value() if keep_value else ""
        blocked = self.blockSignals(True)
        self.clear()
        self._options = []
        for option in options:
            item = {
                "key": str(option.get("key", option.get("label", ""))),
                "label": str(option.get("label", "")),
                "desc": str(option.get("desc") or ""),
                "pills": tuple(option.get("pills") or ()),
            }
            self._options.append(item)
            self.addItem(item["label"], item["key"])
            self.setItemData(self.count() - 1, item, Qt.ItemDataRole.UserRole)
        self.blockSignals(blocked)
        keys = [item["key"] for item in self._options]
        if current and current in keys:
            self.set_value(current)
        elif keys:
            self.setCurrentIndex(0)
        self.update()

    def option(self, key: str | None = None) -> dict:
        target = key if key is not None else self.value()
        for item in self._options:
            if item["key"] == target:
                return item
        return {}

    def options(self) -> list[dict]:
        return list(self._options)

    def value(self) -> str:
        data = self.currentData()
        if isinstance(data, dict):
            return str(data.get("key", ""))
        return str(data) if data is not None else ""

    def set_value(self, value: str, *, emit: bool = False) -> None:
        target = str(value)
        for index, item in enumerate(self._options):
            if item["key"] == target:
                blocked = self.signalsBlocked()
                self.blockSignals(True)
                self.setCurrentIndex(index)
                self.blockSignals(blocked)
                self.update()
                if emit and blocked is False:
                    self.valueChanged.emit(target)
                return

    def _on_index_changed(self, _index: int) -> None:
        if self.signalsBlocked() or not self._options:
            return
        self.update()
        self.valueChanged.emit(self.value())

    # ------------------------------------------------------------- 绘制 --
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        theme = self.theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme.radius("md")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor("surface_raised"))
        painter.drawRoundedRect(rect, radius, radius)

        if self.hasFocus():
            border, width = theme.qcolor("focus"), 2.0
        elif self._hover and self.isEnabled():
            border, width = theme.qcolor("border_strong"), 1.0
        else:
            border, width = theme.qcolor("border"), 1.0
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, width))
        painter.drawRoundedRect(rect, radius, radius)

        item = self.option()
        label_font = theme.font("body_bold") if self.isEnabled() else theme.font("body")
        fm = QFontMetrics(label_font)
        left = theme.px(12)
        chevron_zone = theme.px(34)
        pills = item.get("pills") or ()
        pills_w = _pills_width(theme, pills)
        text_room = int(self.width() - left - pills_w - chevron_zone - theme.px(10))
        painter.setFont(label_font)
        painter.setPen(theme.qcolor("text_primary" if self.isEnabled() else "text_disabled"))
        painter.drawText(
            QRectF(left, 0, max(10, text_room), self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(item.get("label", ""), Qt.TextElideMode.ElideRight, max(10, text_room)),
        )

        if pills:
            _draw_pills(
                painter,
                theme,
                pills,
                self.width() - chevron_zone - theme.px(6),
                self.height() / 2,
                anchor_right=True,
                dim=not self.isEnabled(),
            )

        size = 16
        dpr = float(self.devicePixelRatioF() or 1.0)
        icon = theme.icon("chevron_down", "text_disabled")
        pixmap = icon.pixmap(QSize(size, size), dpr)
        painter.drawPixmap(self.width() - size - theme.px(12), (self.height() - size) // 2, pixmap)
        painter.end()


class Switch(QAbstractButton):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 24)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(theme.duration("fast"))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        theme.changed.connect(lambda: self.update())

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def nextCheckState(self) -> None:  # noqa: N802
        super().nextCheckState()
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if self.isChecked() else 0.0)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        if not self.isEnabled():
            track = self.theme.qcolor("surface_sunken")
            knob = self.theme.qcolor("border_strong")
        elif self.isChecked():
            track = self.theme.qcolor("accent")
            knob = self.theme.qcolor("on_accent")
        else:
            track = self.theme.qcolor("border_strong")
            knob = self.theme.qcolor("surface")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)
        diameter = rect.height() - 6
        x = 3 + self._offset * (rect.width() - diameter - 6)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, 3, diameter, diameter))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.theme.qcolor("focus"), 1.4))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)


class SwitchRow(ThemeAware):
    toggled = Signal(bool)

    def __init__(
        self,
        theme: Theme,
        title: str,
        hint: str = "",
        parent: QWidget | None = None,
        *,
        value: bool = False,
    ) -> None:
        super().__init__(theme, parent)
        loop = QHBoxLayout(self)
        loop.setContentsMargins(0, 0, 0, 0)
        loop.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        self.title = QLabel(title)
        self.title.setFont(theme.font("body"))
        text_box.addWidget(self.title)
        self.hint = QLabel(hint)
        self.hint.setObjectName("Caption")
        self.hint.setVisible(bool(hint))
        text_box.addWidget(self.hint)
        loop.addLayout(text_box, 1)
        self.switch = Switch(theme)
        self.switch.setChecked(value)
        self.switch.toggled.connect(self.toggled.emit)
        loop.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> bool:
        return self.switch.isChecked()

    def set_value(self, value: bool, *, emit: bool = False) -> None:
        previous = self.switch.blockSignals(True)
        self.switch.setChecked(value)
        self.switch.blockSignals(previous)
        self.switch.set_offset(1.0 if value else 0.0)
        if emit:
            self.toggled.emit(value)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.switch.toggle()
        super().mouseReleaseEvent(event)

    def update_theme(self) -> None:
        self.title.setFont(self.theme.font("body"))

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.title.setObjectName("Muted" if not enabled else "")
        repolish(self.title)
        self.hint.setObjectName("Disabled" if not enabled else "Caption")
        repolish(self.hint)


# ---------------------------------------------------------------- 滑块行 --
class SliderRow(ThemeAware):
    valueChanged = Signal(int)

    def __init__(self, theme: Theme, title: str, parent: QWidget | None = None, *, value: int = 90) -> None:
        super().__init__(theme, parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title = QLabel(title)
        self.title.setFont(theme.font("body"))
        self.badge = QLabel(str(value))
        self.badge.setObjectName("AccentText")
        self.badge.setFont(theme.font("caption_medium"))
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(38, 22)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.badge)
        box.addLayout(header)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(self._on_value)
        box.addWidget(self.slider)

    def _on_value(self, value: int) -> None:
        self.badge.setText(str(value))
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int, *, emit: bool = False) -> None:
        previous = self.slider.blockSignals(True)
        self.slider.setValue(int(value))
        self.slider.blockSignals(previous)
        self.badge.setText(str(int(value)))
        if emit:
            self.valueChanged.emit(int(value))

    def update_theme(self) -> None:
        self.title.setFont(self.theme.font("body"))
        self.badge.setFont(self.theme.font("caption_medium"))


# -------------------------------------------------------------- 拖放空态 --
class DropZone(ThemeAware):
    """空列表时的拖放区：虚线圆角 + 图标 + 主副标题。"""

    clicked = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.title = "把图片拖到这里"
        self.subtitle = "支持 PNG · JPG · WebP · GIF · BMP · TIFF · AVIF · ICO"
        self.hint = "也可以点击「添加图片」，或按 Ctrl+V 粘贴"
        self._active = False
        self._hover = False
        self.setMinimumHeight(280)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.theme.radius("lg")
        active = self._active
        border = self.theme.qcolor("accent" if active else ("border_strong" if self._hover else "border"))
        pen = QPen(border, 1.4 if active else 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        painter.setPen(pen)
        painter.setBrush(self.theme.qcolor("accent_subtle" if active else "surface"))
        painter.drawRoundedRect(rect, radius, radius)

        center = rect.center()
        icon_size = 44
        icon_rect = QRectF(center.x() - icon_size / 2, center.y() - 74, icon_size, icon_size)
        icon_color = self.theme.qcolor("accent" if active else "text_disabled")
        icons.draw(painter, "image", icon_rect, icon_color, 1.5)

        painter.setPen(self.theme.qcolor("text_primary"))
        painter.setFont(self.theme.font("h2"))
        title_rect = QRectF(rect.left(), center.y() - 14, rect.width(), 26)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "松开即可添加" if active else self.title)

        painter.setPen(self.theme.qcolor("text_secondary"))
        painter.setFont(self.theme.font("body"))
        sub_rect = QRectF(rect.left(), center.y() + 16, rect.width(), 22)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, self.subtitle)

        painter.setPen(self.theme.qcolor("text_disabled"))
        painter.setFont(self.theme.font("caption"))
        hint_rect = QRectF(rect.left(), center.y() + 42, rect.width(), 20)
        painter.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, self.hint)


# ------------------------------------------------------------- 进度条 --
class MiniProgress(QWidget):
    """4px 圆角进度条；不确定进度时用滑动光块，而不是转圈。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._value = 0.0
        self._indeterminate = False
        self._phase = 0.0
        self.setFixedHeight(6)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        theme.changed.connect(lambda: self.update())

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def set_indeterminate(self, on: bool) -> None:
        if on == self._indeterminate:
            return
        self._indeterminate = on
        if on:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.theme.qcolor("surface_sunken"))
        painter.drawRoundedRect(rect, radius, radius)
        if self._indeterminate:
            width = rect.width() * 0.28
            x = -width + (rect.width() + width) * self._phase
            gradient = QLinearGradient(x, 0, x + width, 0)
            color = QColor(self.theme.qcolor("accent"))
            transparent = QColor(color)
            transparent.setAlpha(0)
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(0.5, color)
            gradient.setColorAt(1.0, transparent)
            painter.setBrush(gradient)
            painter.drawRoundedRect(QRectF(x, 0, width, rect.height()), radius, radius)
        elif self._value > 0:
            # 从左侧开始累积填充：进度再小也保留一小段可见的色块。
            filled = max(rect.width() * self._value, radius * 2)
            painter.setBrush(self.theme.qcolor("accent"))
            painter.drawRoundedRect(QRectF(0, 0, min(filled, rect.width()), rect.height()), radius, radius)


# ------------------------------------------------------------------ Toast --
class Toast(QLabel):
    """窗口内浮层提示：淡入 → 停留 → 淡出，不打断操作。"""

    def __init__(self, parent: QWidget, theme: Theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(theme.font("body_medium"))
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(theme.duration("base"))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, kind: str = "info", duration: int = 2200) -> None:
        colors = {
            "info": ("surface_raised", "text_primary", "border"),
            "success": ("success_subtle", "success", "success"),
            "error": ("danger_subtle", "danger", "danger"),
            "warning": ("warning_subtle", "warning", "warning"),
        }
        bg, fg, border = colors.get(kind, colors["info"])
        self.setText(text)
        self.adjustSize()
        radius = max(self.theme.radius("md"), self.height() // 2)
        self.setStyleSheet(
            f"QLabel#Toast {{ background: {self.theme.color(bg)}; color: {self.theme.color(fg)};"
            f" border: 1px solid {self.theme.color(border)}; border-radius: {radius}px;"
            f" padding: 8px 18px; }}"
        )
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(duration)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 88
        self.move(max(12, x), max(12, y))

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._hide_once)
        self._anim.start()

    def _hide_once(self) -> None:
        try:
            self._anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        self.setVisible(False)


# ------------------------------------------------------------------ 其它 --
class ElidedLabel(QLabel):
    """中英文混排按宽度省略号截断。"""

    def __init__(self, text: str = "", parent: QWidget | None = None, mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> None:
        super().__init__(parent)
        self._full_text = text
        self._mode = mode
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        super().setText(text)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        text = metrics.elidedText(self._full_text, self._mode, self.width())
        painter.drawText(self.rect(), int(self.alignment()), text)
