"""文件队列：QAbstractListModel + 自绘 delegate + 缩略图异步加载。

列表是应用的主视觉，行内信息按“缩略图 → 文件名 → 规格 → 状态”排布；
所有颜色/字号/间距都来自 theme token，八态（hover / selected / running /
done / failed / disabled / empty / drag）都画全。
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import converter, ico
from . import icons
from .theme import Theme
from .widgets import Card, DropZone

ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
THUMB_SIZE = 40
ROW_HEIGHT = 60

STATUS_TEXT = {
    "reading": "读取中",
    "idle": "就绪",
    "running": "处理中",
    "done": "完成",
    "failed": "失败",
    "skipped": "已跳过",
}


@dataclass
class QueueItem:
    path: Path
    status: str = "reading"
    info: converter.ImageInfo | None = None
    result: object | None = None
    thumb: QPixmap | None = None
    message: str = ""
    detail: str = ""
    kind: str = "image"
    meta_override: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    def meta_text(self, theme: Theme) -> str:
        if self.meta_override:
            return self.meta_override
        if self.info is None or not self.info.ok:
            return self.message or "正在读取…"
        parts = [f"{self.info.width}×{self.info.height}"]
        if self.info.frames > 1:
            parts.append(f"{self.info.frames} 帧")
        parts.append(converter.human_size(self.info.bytes))
        parts.append(self.info.format or self.path.suffix.lstrip(".").upper())
        return " · ".join(parts)

    def result_text(self) -> str:
        if self.result is None:
            return ""
        if not self.result.ok:
            return self.result.message or "失败"
        dest = getattr(self.result, "dest", None)
        size = getattr(self.result, "bytes", 0)
        if not size and dest is not None and dest.exists():
            size = dest.stat().st_size
        if hasattr(self.result, "duration"):  # 视频结果
            return f"{self.result.width}×{self.result.height} · {converter.human_size(size)}"
        if hasattr(self.result, "model_scale"):  # 超分结果
            return f"{self.result.width}×{self.result.height} · {converter.human_size(size)}"
        spec = converter.FORMATS[dest.suffix.lstrip(".").lower()] if dest else None
        label = spec.label if spec else ""
        if spec is not None and spec.key == "ico" and dest is not None:
            try:
                count = len(ico.read_entries(dest))
            except Exception:
                count = 0
            if count:
                return f"{count} 个尺寸 · {converter.human_size(size)}"
        return f"{label} · {converter.human_size(size)}"


class FileModel(QAbstractListModel):
    def __init__(self, theme: Theme, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.items: list[QueueItem] = []
        self._index: dict[str, int] = {}

    # ---------------------------------------------------------- Qt 接口 --
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not (0 <= index.row() < len(self.items)):
            return None
        item = self.items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == int(Qt.ItemDataRole.DisplayRole):
            return item.name
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return str(item.path) + ("\n" + item.message if item.message else "")
        return None

    # ---------------------------------------------------------- 业务接口 --
    def add_paths(self, paths: Iterable[Path]) -> list[int]:
        added: list[int] = []
        existing = set(self._index)
        for raw in paths:
            path = Path(raw)
            key = str(path).lower()
            if key in existing:
                continue
            existing.add(key)
            row = len(self.items)
            self.beginInsertRows(QModelIndex(), row, row)
            self.items.append(QueueItem(path=path))
            self._index[key] = row
            self.endInsertRows()
            added.append(row)
        return added

    def row_of(self, path: Path) -> int | None:
        return self._index.get(str(path).lower())

    def item(self, row: int) -> QueueItem | None:
        if 0 <= row < len(self.items):
            return self.items[row]
        return None

    def paths(self) -> list[Path]:
        return [item.path for item in self.items]

    def set_status(self, row: int, status: str, *, message: str = "", detail: str = "") -> None:
        item = self.item(row)
        if item is None:
            return
        item.status = status
        if message:
            item.message = message
        if detail:
            item.detail = detail
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [ITEM_ROLE])

    def set_info(self, row: int, info: converter.ImageInfo, thumb: QPixmap | None = None) -> None:
        item = self.item(row)
        if item is None:
            return
        item.info = info
        item.status = "idle" if info.ok else "failed"
        item.message = "" if info.ok else (info.message or "读取失败")
        if thumb is not None:
            item.thumb = thumb
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [ITEM_ROLE])

    def set_thumb(self, row: int, thumb: QPixmap) -> None:
        item = self.item(row)
        if item is None:
            return
        item.thumb = thumb
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [ITEM_ROLE])

    def set_result(self, row: int, result: object) -> None:
        item = self.item(row)
        if item is None:
            return
        item.result = result
        if result.ok:
            item.status = "done"
            item.message = ""
        else:
            item.status = "failed"
            item.message = result.message
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [ITEM_ROLE])

    def remove_rows(self, rows: Sequence[int]) -> list[str]:
        removed: list[str] = []
        for row in sorted(set(rows), reverse=True):
            item = self.item(row)
            if item is None:
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            self.items.pop(row)
            removed.append(str(item.path).lower())
            self.endRemoveRows()
        for key in removed:
            self._index.pop(key, None)
        self._reindex()
        return removed

    def _reindex(self) -> None:
        self._index = {str(item.path).lower(): row for row, item in enumerate(self.items)}

    def clear(self) -> None:
        if not self.items:
            return
        self.beginResetModel()
        self.items.clear()
        self._index.clear()
        self.endResetModel()

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def refresh_all(self) -> None:
        if not self.items:
            return
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, 0), [ITEM_ROLE])


# ---------------------------------------------------------------- 缩略图 --
class ThumbnailTask(QRunnable):
    """Pillow 解码 → QPixmap；用 draft 让 JPEG 走 DCT 缩放的快速路径。"""

    class Signals(QObject):
        done = Signal(int, QPixmap)
        failed = Signal(int, str)

    def __init__(self, row: int, path: Path) -> None:
        super().__init__()
        self.row = row
        self.path = path
        self.signals = ThumbnailTask.Signals()

    def run(self) -> None:  # noqa: D401
        try:
            from PIL import Image

            with Image.open(self.path) as img:
                if img.format in ("JPEG", "MPO"):
                    img.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
                img.thumbnail((THUMB_SIZE * 2, THUMB_SIZE * 2))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA" if "A" in img.mode or "transparency" in img.info else "RGB")
                data = img.tobytes("raw", "RGBA" if img.mode == "RGBA" else "RGB")
                fmt = QImage.Format.Format_RGBA8888 if img.mode == "RGBA" else QImage.Format.Format_RGB888
                image = QImage(data, img.width, img.height, img.width * (4 if img.mode == "RGBA" else 3), fmt).copy()
            pixmap = QPixmap.fromImage(image)
            self.signals.done.emit(self.row, pixmap)
        except Exception as exc:  # noqa: BLE001 - 缩略图失败不影响主流程
            self.signals.failed.emit(self.row, str(exc))


# ---------------------------------------------------------------- delegate --
class FileDelegate(QStyledItemDelegate):
    def __init__(self, theme: Theme, view: QListView) -> None:
        super().__init__(view)
        self.theme = theme
        self.view = view
        self.hovered_row = -1
        self.remove_hovered = False

    # -------------------------------------------------------- 几何 --
    def _remove_rect_local(self, option) -> QRectF:
        return QRectF(option.rect.right() - 24 - 14, option.rect.center().y() - 12, 24, 24)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_HEIGHT)

    def _status_colors(self, status: str) -> tuple[QColor, QColor]:
        mapping = {
            "done": ("success_subtle", "success"),
            "failed": ("danger_subtle", "danger"),
            "running": ("accent_subtle", "accent_text"),
            "reading": ("surface_sunken", "text_secondary"),
            "skipped": ("surface_sunken", "text_disabled"),
            "idle": ("surface_sunken", "text_secondary"),
        }
        bg, fg = mapping.get(status, ("surface_sunken", "text_secondary"))
        return self.theme.qcolor(bg), self.theme.qcolor(fg)

    def paint(self, painter: QPainter, option, index) -> None:
        item: QueueItem | None = index.data(ITEM_ROLE)
        if item is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = QRectF(option.rect)
        row = QRectF(outer.left() + 8, outer.top() + 3, outer.width() - 16, outer.height() - 6)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.hovered_row

        radius = self.theme.radius("md")
        if selected:
            painter.setBrush(self.theme.qcolor("accent_subtle"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(row, radius, radius)
            painter.setBrush(self.theme.qcolor("accent"))
            painter.drawRoundedRect(QRectF(row.left(), row.top() + 10, 3, row.height() - 20), 1.5, 1.5)
        elif hovered:
            painter.setBrush(self.theme.qcolor("surface_hover"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(row, radius, radius)

        # 缩略图
        thumb_rect = QRectF(row.left() + 12, row.top() + (row.height() - THUMB_SIZE) / 2, THUMB_SIZE, THUMB_SIZE)
        self._paint_thumb(painter, thumb_rect, item)

        # 文字区
        text_left = thumb_rect.right() + 12
        status_width = 78
        remove_width = 34 if (hovered and item.status not in ("running",)) else 0
        text_width = row.right() - status_width - remove_width - text_left - 8

        name_rect = QRectF(text_left, row.top() + 9, max(40.0, text_width), 20)
        painter.setFont(self.theme.font("body_medium"))
        painter.setPen(self.theme.qcolor("text_primary" if item.status != "failed" else "text_primary"))
        metrics = QFontMetrics(painter.font())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(item.name, Qt.TextElideMode.ElideMiddle, int(name_rect.width())))

        meta_rect = QRectF(text_left, row.top() + 30, max(40.0, text_width), 18)
        painter.setFont(self.theme.font("caption"))
        meta_color = self.theme.qcolor("text_secondary" if item.status != "failed" else "danger")
        painter.setPen(meta_color)
        meta_metrics = QFontMetrics(painter.font())
        painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         meta_metrics.elidedText(self._meta_line(item), Qt.TextElideMode.ElideRight, int(meta_rect.width())))

        # 状态胶囊
        bg, fg = self._status_colors(item.status)
        label = STATUS_TEXT.get(item.status, item.status)
        if item.status == "running" and item.detail:
            label = item.detail
        pill = self._pill_rect(row, status_width, label)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        painter.setFont(self.theme.font("caption_medium"))
        painter.setPen(fg)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

        # 移除按钮（悬停才出现）
        if remove_width:
            remove_rect = QRectF(row.right() - remove_width + 2, row.center().y() - 12, 24, 24)
            if self.remove_hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.theme.qcolor("surface_sunken"))
                painter.drawRoundedRect(remove_rect, 12, 12)
            icons.draw(painter, "close", remove_rect.adjusted(6, 6, -6, -6),
                       self.theme.qcolor("text_secondary"), 1.8)
        painter.restore()

    def _pill_rect(self, row: QRectF, width: float, label: str) -> QRectF:
        metrics = QFontMetrics(self.theme.font("caption_medium"))
        width = min(width, metrics.horizontalAdvance(label) + 20)
        width = max(width, 56)
        height = 22
        return QRectF(row.right() - width - 10, row.center().y() - height / 2, width, height)

    def _meta_line(self, item: QueueItem) -> str:
        if item.status == "failed" and item.message:
            return item.message
        if item.status == "done":
            return item.result_text()
        if item.status == "running" and item.result:
            return item.result_text()
        return item.meta_text(self.theme)

    def _paint_thumb(self, painter: QPainter, rect: QRectF, item: QueueItem) -> None:
        radius = 8.0
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        # 透明图用棋盘格打底
        painter.fillRect(rect, self.theme.qcolor("surface_sunken"))
        cell = 6.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.theme.qcolor("checker_a"))
        painter.drawRect(rect)
        painter.setBrush(self.theme.qcolor("checker_b"))
        rows = int(rect.height() // cell) + 1
        cols = int(rect.width() // cell) + 1
        for r in range(rows):
            for c in range(cols):
                if (r + c) % 2 == 0:
                    painter.drawRect(QRectF(rect.left() + c * cell, rect.top() + r * cell, cell, cell))
        if item.thumb is not None:
            pix = item.thumb
            scaled = pix.scaled(int(rect.width()), int(rect.height()),
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            x = rect.left() + (rect.width() - scaled.width()) / 2
            y = rect.top() + (rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
        painter.restore()
        painter.setPen(self.theme.qcolor("border"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)

    # -------------------------------------------------------- 交互 --
    def editorEvent(self, event, model, option, index):  # noqa: N802
        if index.isValid() and event.type() == QEvent.Type.MouseButtonRelease:
            pos = event.position().toPoint()
            item: QueueItem | None = index.data(ITEM_ROLE)
            if item is not None and item.status != "running":
                remove = self._remove_rect_local(option)
                if remove.contains(pos):
                    self.view.remove_requested.emit(index.row())
                    return True
        return super().editorEvent(event, model, option, index)


# ----------------------------------------------------------------- 列表 --
class FileList(QListView):
    remove_requested = Signal(int)
    preview_requested = Signal(int)
    files_dropped = Signal(list)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("FileList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.verticalScrollBar().setSingleStep(24)
        self._delegate = FileDelegate(theme, self)
        self.setItemDelegate(self._delegate)
        self.doubleClicked.connect(lambda index: self.preview_requested.emit(index.row()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        change = row != self._delegate.hovered_row
        if change:
            self._delegate.hovered_row = row
            self.viewport().update()
        remove_hover = False
        if index.isValid():
            option_rect = self.visualRect(index)
            remove = QRectF(option_rect.right() - 24 - 14, option_rect.center().y() - 12, 24, 24)
            remove_hover = remove.contains(event.position())
        if remove_hover != self._delegate.remove_hovered:
            self._delegate.remove_hovered = remove_hover
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if remove_hover else Qt.CursorShape.ArrowCursor)
            self.viewport().update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._delegate.hovered_row = -1
        self._delegate.remove_hovered = False
        self.viewport().unsetCursor()
        self.viewport().update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            rows = self.selected_rows()
            if rows:
                for row in sorted(rows, reverse=True):
                    self.remove_requested.emit(row)
                return
        if event.key() == Qt.Key.Key_Return and self.currentIndex().isValid():
            self.preview_requested.emit(self.currentIndex().row())
            return
        super().keyPressEvent(event)

    def selected_rows(self) -> list[int]:
        return sorted(index.row() for index in self.selectionModel().selectedRows())


# ------------------------------------------------------------- 卡片容器 --
class FilePanel(QWidget):
    """待处理卡片：标题栏 + 空态 / 列表切换。"""

    add_clicked = Signal()
    remove_requested = Signal(int)
    preview_requested = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = Card(theme, padding=18, spacing=14)
        layout.addWidget(self.card)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("待处理")
        title.setFont(theme.font("h2"))
        self.count_label = QLabel("")
        self.count_label.setObjectName("Caption")
        self.count_label.setFont(theme.font("body"))
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch(1)
        self._hint_text = "双击预览 · Delete 移除"
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("Caption")
        header.addWidget(self.hint_label)
        self.card.body.addLayout(header)

        self.drop_zone = DropZone(theme)
        self.drop_zone.clicked.connect(self.add_clicked)
        self.list = FileList(theme)
        self.list.remove_requested.connect(self.remove_requested)
        self.list.preview_requested.connect(self.preview_requested)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.drop_zone)
        self.stack.addWidget(self.list)
        self.card.body.addWidget(self.stack, 1)

    def show_list(self) -> None:
        self.stack.setCurrentIndex(1)

    def show_empty(self) -> None:
        self.stack.setCurrentIndex(0)

    def set_hint(self, text: str) -> None:
        self._hint_text = text
        if self.list.isVisible():
            self.hint_label.setText(text)

    def set_counts(self, total: int, done: int, failed: int) -> None:
        if total == 0:
            self.count_label.setText("")
            self.hint_label.setText("")
            return
        text = f"{total} 张"
        if done:
            text += f" · 完成 {done}"
        if failed:
            text += f" · 失败 {failed}"
        self.count_label.setText(text)
        self.hint_label.setText(self._hint_text if total else "")
