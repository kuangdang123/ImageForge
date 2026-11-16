"""从文件夹里挑图：搜索 + 多选（单击 / Shift 范围 / 全选）+ 添加并转换。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import converter
from . import icons
from .theme import Theme
from .widgets import Chip, ElidedLabel

PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1
META_ROLE = int(Qt.ItemDataRole.UserRole) + 2
TAKEN_ROLE = int(Qt.ItemDataRole.UserRole) + 3
ROW_HEIGHT = 52
MAX_ROWS = 800


def natural_key(name: str) -> list:
    import re

    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


# --------------------------------------------------------------- 缩略图 --
class ThumbTask(QRunnable):
    class Signals(QObject):
        done = Signal(str, object)

    def __init__(self, path: Path, size: int = 72) -> None:
        super().__init__()
        self.path = path
        self.size = size
        self.signals = ThumbTask.Signals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            scaled = size.scaled(QSize(self.size * 2, self.size * 2), Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(scaled)
        image = reader.read()
        if image.isNull():
            self.signals.done.emit(str(self.path), None)
        else:
            self.signals.done.emit(str(self.path), image)


# ----------------------------------------------------------------- 委托 --
class PickerDelegate(QStyledItemDelegate):
    def __init__(self, theme: Theme, view: QListWidget) -> None:
        super().__init__(view)
        self.theme = theme
        self.view = view
        self.hovered = -1
        self.thumbs: dict[str, QPixmap] = {}

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        theme = self.theme
        item = self.view.item(index.row())
        if item is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(6, 3, -6, -3)
        checked = item.checkState() == Qt.CheckState.Checked
        taken = bool(item.data(TAKEN_ROLE))
        hovered = index.row() == self.hovered

        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor("accent_subtle"))
            painter.drawRoundedRect(rect, theme.radius("md"), theme.radius("md"))
            painter.setBrush(theme.qcolor("accent"))
            painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 9, 3, rect.height() - 18), 1.5, 1.5)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor("surface_hover"))
            painter.drawRoundedRect(rect, theme.radius("md"), theme.radius("md"))

        # 勾选框
        box = QRectF(rect.left() + 14, rect.center().y() - 9, 18, 18)
        if checked:
            painter.setBrush(theme.qcolor("accent"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(box, 5, 5)
            icons.draw(
                painter,
                "check",
                box.adjusted(3.5, 3.5, -3.5, -3.5),
                theme.qcolor("on_accent"),
                2.2,
            )
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(theme.qcolor("border_strong" if not taken else "border"), 1.4)
            painter.setPen(pen)
            painter.drawRoundedRect(box, 5, 5)

        # 缩略图
        thumb_rect = QRectF(box.right() + 14, rect.center().y() - 17, 34, 34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor("surface_sunken"))
        painter.drawRoundedRect(thumb_rect, 7, 7)
        pix = self.thumbs.get(str(item.data(PATH_ROLE)))
        if pix is not None and not pix.isNull():
            scaled = pix.scaled(
                int(thumb_rect.width()) - 4,
                int(thumb_rect.height()) - 4,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = thumb_rect.left() + (thumb_rect.width() - scaled.width()) / 2
            y = thumb_rect.top() + (thumb_rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)

        # 文本
        text_left = thumb_rect.right() + 12
        right_pad = 96 if taken else 12
        text_width = max(60.0, rect.right() - right_pad - text_left)
        painter.setFont(theme.font("body_medium" if not taken else "body"))
        painter.setPen(theme.qcolor("text_disabled" if taken else "text_primary"))
        name_metrics = QFontMetrics(painter.font())
        name_rect = QRectF(text_left, rect.top() + 8, text_width, 20)
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name_metrics.elidedText(item.text(), Qt.TextElideMode.ElideMiddle, int(text_width)),
        )

        meta = str(item.data(META_ROLE) or "")
        painter.setFont(theme.font("caption"))
        painter.setPen(theme.qcolor("text_disabled" if taken else "text_secondary"))
        meta_metrics = QFontMetrics(painter.font())
        meta_rect = QRectF(text_left, rect.top() + 28, text_width, 16)
        painter.drawText(
            meta_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            meta_metrics.elidedText(meta, Qt.TextElideMode.ElideRight, int(text_width)),
        )

        if taken:
            badge = QRectF(rect.right() - 88, rect.center().y() - 10, 78, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor("surface_sunken"))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setFont(theme.font("caption"))
            painter.setPen(theme.qcolor("text_disabled"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "已在列表")
        painter.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        return False


# ----------------------------------------------------------------- 列表 --
class PickerList(QListWidget):
    order_changed = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._anchor = -1
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSpacing(0)
        self.delegate = PickerDelegate(theme, self)
        self.setItemDelegate(self.delegate)
        self.verticalScrollBar().setSingleStep(26)

    # 单击切换；Shift 范围；双击只加这一张
    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            super().mousePressEvent(event)
            return
        row = index.row()
        item = self.item(row)
        if item is None or bool(item.data(TAKEN_ROLE)):
            self._anchor = row
            return
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._anchor >= 0:
            self._set_range(self._anchor, row, not self._is_checked(row))
        else:
            self._toggle(row)
            self._anchor = row
        self.viewport().update()
        self.order_changed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        super().wheelEvent(event)
        self.viewport().update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        if row != self.delegate.hovered:
            self.delegate.hovered = row
            self.viewport().update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.delegate.hovered = -1
        self.viewport().update()
        super().leaveEvent(event)

    def _is_checked(self, row: int) -> bool:
        item = self.item(row)
        return bool(item and item.checkState() == Qt.CheckState.Checked)

    def _toggle(self, row: int) -> None:
        item = self.item(row)
        if item is None or item.isHidden() or bool(item.data(TAKEN_ROLE)):
            return
        item.setCheckState(Qt.CheckState.Unchecked if self._is_checked(row) else Qt.CheckState.Checked)

    def _set_range(self, start: int, end: int, checked: bool) -> None:
        step = 1 if end >= start else -1
        for row in range(start, end + step, step):
            item = self.item(row)
            if item is None or item.isHidden() or bool(item.data(TAKEN_ROLE)):
                continue
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def set_row_checked(self, row: int, checked: bool) -> None:
        item = self.item(row)
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def visible_rows(self) -> list[int]:
        return [row for row in range(self.count()) if not self.item(row).isHidden()]

    def checked_rows(self) -> list[int]:
        return [
            row
            for row in range(self.count())
            if self.item(row).checkState() == Qt.CheckState.Checked and not self.item(row).isHidden()
        ]

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space,):
            row = self.currentRow()
            if row >= 0:
                self._toggle(row)
                self.order_changed.emit()
                return
        if event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            for row in self.visible_rows():
                self.set_row_checked(row, True)
            self.viewport().update()
            self.order_changed.emit()
            return
        super().keyPressEvent(event)


# ----------------------------------------------------------------- 主窗 --
class FolderPicker(QDialog):
    def __init__(
        self,
        parent: QWidget,
        theme: Theme,
        folder: Path,
        *,
        existing: set[Path] | None = None,
        recursive: bool = False,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.folder = Path(folder)
        self.existing = {str(p).lower() for p in (existing or set())}
        self._recursive = recursive
        self._start_requested = False
        self._selected: list[Path] = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._all_paths: list[Path] = []

        self.setWindowTitle("选择图片")
        self.setWindowIcon(icons.icon("folder", theme.color("accent"), 20))
        self.resize(920, 680)
        self.setMinimumSize(720, 500)
        self._build()
        self._refresh()

    # ------------------------------------------------------------- 构建 --
    def _build(self) -> None:
        theme = self.theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("folder", theme.color("accent"), 22))
        header.addWidget(icon_label)
        self.path_label = ElidedLabel(str(self.folder), mode=Qt.TextElideMode.ElideMiddle)
        self.path_label.setFont(theme.font("body_bold"))
        header.addWidget(self.path_label, 1)
        self.change_btn = QPushButton(" 更改文件夹…")
        self.change_btn.setProperty("variant", "ghost")
        self.change_btn.setIcon(icons.icon("folder", theme.color("text_secondary"), 16))
        self.change_btn.setIconSize(QSize(16, 16))
        self.change_btn.clicked.connect(self._change_folder)
        header.addWidget(self.change_btn)
        layout.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索文件名…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)

        self.recursive_chip = Chip(theme, "包含子文件夹")
        self.recursive_chip.setCheckable(True)
        self.recursive_chip.setChecked(self._recursive)
        self.recursive_chip.clicked.connect(self._toggle_recursive)
        filters.addWidget(self.recursive_chip)

        self.all_btn = QPushButton("全选")
        self.all_btn.setProperty("variant", "ghost")
        self.all_btn.clicked.connect(self._select_all)
        filters.addWidget(self.all_btn)

        self.none_btn = QPushButton("清空")
        self.none_btn.setProperty("variant", "ghost")
        self.none_btn.clicked.connect(self._select_none)
        filters.addWidget(self.none_btn)
        layout.addLayout(filters)

        self.list = PickerList(theme)
        self.list.order_changed.connect(self._update_footer)
        layout.addWidget(self.list, 1)

        self.empty_label = QLabel("这个文件夹里没有支持的图片")
        self.empty_label.setObjectName("Caption")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Caption")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        close_btn = QPushButton("取消")
        close_btn.setProperty("variant", "ghost")
        close_btn.clicked.connect(self.reject)
        self.confirm_btn = QPushButton("添加选中")
        self.confirm_btn.setProperty("variant", "secondary")
        self.confirm_btn.clicked.connect(lambda: self._confirm(False))
        self.start_btn = QPushButton("添加并转换")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(lambda: self._confirm(True))
        footer.addWidget(close_btn)
        footer.addWidget(self.confirm_btn)
        footer.addWidget(self.start_btn)
        layout.addLayout(footer)

    # ------------------------------------------------------------- 扫描 --
    def _refresh(self) -> None:
        self.list.clear()
        self._all_paths = []
        try:
            found = converter.collect_images(self.folder, recursive=self._recursive)
        except Exception:  # noqa: BLE001
            found = []
        found = sorted(found, key=lambda p: (str(p.parent).lower(), natural_key(p.name)))[:MAX_ROWS]
        self._all_paths = found

        for path in found:
            item = QListWidgetItem(path.name)
            item.setData(PATH_ROLE, str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            key = str(path).lower()
            taken = key in self.existing
            item.setData(TAKEN_ROLE, taken)
            item.setData(META_ROLE, "已在列表" if taken else self._meta_for(path))
            if taken:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        self.empty_label.setVisible(not found)
        self.list.setVisible(bool(found))
        self._schedule_thumbs(found)
        self._apply_filter(self.search.text())
        self._update_footer()

    def _meta_for(self, path: Path) -> str:
        info = converter.probe(path)
        if not info.ok:
            return info.message or "读取失败"
        parts = [f"{info.width}×{info.height}", converter.human_size(info.bytes)]
        if info.frames > 1:
            parts.append(f"{info.frames} 帧")
        return " · ".join(parts)

    def _schedule_thumbs(self, paths: list[Path]) -> None:
        for path in paths:
            task = ThumbTask(path)
            task.signals.done.connect(self._on_thumb)
            self._pool.start(task)

    def _on_thumb(self, path: str, image) -> None:
        if image is None:
            return
        pixmap = QPixmap.fromImage(image)
        self.list.delegate.thumbs[path] = pixmap
        self.list.viewport().update()

    # ------------------------------------------------------------- 交互 --
    def _toggle_recursive(self) -> None:
        self._recursive = self.recursive_chip.isChecked()
        self._refresh()

    def _change_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", str(self.folder))
        if folder:
            self.folder = Path(folder)
            self.path_label.setText(str(self.folder))
            self.path_label.setToolTip(str(self.folder))
            self._refresh()

    def _apply_filter(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            visible = not query or query in item.text().casefold()
            item.setHidden(not visible)
        self._update_footer()
        self.list.viewport().update()

    def _select_all(self) -> None:
        for row in self.list.visible_rows():
            self.list.set_row_checked(row, True)
        self.list.viewport().update()
        self._update_footer()

    def _select_none(self) -> None:
        for row in range(self.list.count()):
            self.list.set_row_checked(row, False)
        self.list.viewport().update()
        self._update_footer()

    def _update_footer(self) -> None:
        checked = self.list.checked_rows()
        total = sum(1 for row in range(self.list.count()) if not self.list.item(row).isHidden())
        self.count_label.setText(f"{total} 张图片 · 已选 {len(checked)}")
        self.confirm_btn.setEnabled(bool(checked))
        self.start_btn.setEnabled(bool(checked))
        self.confirm_btn.setText(f"添加选中 · {len(checked)}" if checked else "添加选中")

    def _confirm(self, start: bool) -> None:
        rows = self.list.checked_rows()
        paths: list[Path] = []
        for row in rows:
            item = self.list.item(row)
            raw = item.data(PATH_ROLE) if item is not None else None
            if raw:
                paths.append(Path(str(raw)))
        if not paths:
            return
        self._selected = paths
        self._start_requested = start
        self.accept()

    # ------------------------------------------------------------- 结果 --
    def selected_paths(self) -> list[Path]:
        return list(self._selected)

    def start_requested(self) -> bool:
        return self._start_requested

    def recursive(self) -> bool:
        return self._recursive
