"""预览窗口：大图 + 元信息 +（ICO 时）各尺寸渲染条。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import converter, ico
from . import icons
from .queueview import QueueItem
from .theme import Theme


class ImageCanvas(QWidget):
    """棋盘格底 + 等比居中的大图。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.image: QPixmap | None = None
        self.setMinimumSize(360, 260)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())

    def set_image(self, pixmap: QPixmap | None) -> None:
        self.image = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = self.theme.radius("md")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.theme.qcolor("surface_sunken"))
        painter.drawRoundedRect(rect, radius, radius)

        if self.image is None or self.image.isNull():
            painter.setPen(self.theme.qcolor("text_secondary"))
            painter.setFont(self.theme.font("caption"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "无法生成预览")
            return

        available = rect.adjusted(20, 20, -20, -20)
        # 放大用整数倍 + 最近邻（像素锐利），只有缩小时才用平滑。
        src_w = max(1, self.image.width())
        limit = min(available.width(), available.height())
        max_zoom = 4 if src_w <= 64 else 2
        zoom = 1
        while zoom < max_zoom and src_w * (zoom + 1) <= limit:
            zoom += 1
        if src_w <= limit:
            target = self.image.size() * zoom
            mode = Qt.TransformationMode.FastTransformation
        else:
            target = available.size().toSize()
            mode = Qt.TransformationMode.SmoothTransformation
        scaled = self.image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, mode)
        x = available.left() + (available.width() - scaled.width()) / 2
        y = available.top() + (available.height() - scaled.height()) / 2
        image_rect = QRectF(x, y, scaled.width(), scaled.height())

        # 棋盘格只铺在图片区域，避免小图标落在一大片网格里显得空荡。
        board = image_rect.adjusted(-10, -10, 10, 10)
        board_radius = self.theme.radius("sm")
        painter.setBrush(self.theme.qcolor("checker_a"))
        painter.drawRoundedRect(board, board_radius, board_radius)
        painter.save()
        painter.setClipRect(board)
        cell = 10.0
        painter.setBrush(self.theme.qcolor("checker_b"))
        rows = int(board.height() // cell) + 1
        cols = int(board.width() // cell) + 1
        for r in range(rows):
            for c in range(cols):
                if (r + c) % 2 == 0:
                    painter.drawRect(QRectF(board.left() + c * cell, board.top() + r * cell, cell, cell))
        painter.restore()

        painter.setPen(QPen(self.theme.qcolor("border"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(board, board_radius, board_radius)
        painter.drawPixmap(int(x), int(y), scaled)


class PreviewDialog(QDialog):
    def __init__(self, parent: QWidget, theme: Theme, item: QueueItem) -> None:
        super().__init__(parent)
        self.theme = theme
        self.item = item
        self.setWindowTitle(f"预览 · {item.path.name}")
        self.setWindowIcon(icons.icon("image", theme.color("accent"), 20))
        self.resize(860, 660)
        self.setMinimumSize(640, 480)
        self._build()
        self._load()

    def _build(self) -> None:
        theme = self.theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel(self.item.path.name)
        title.setFont(theme.font("h2"))
        layout.addWidget(title)

        path_label = QLabel(str(self.item.path))
        path_label.setObjectName("Caption")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)

        self.canvas = ImageCanvas(theme)
        layout.addWidget(self.canvas, 1)

        self.meta = QLabel("")
        self.meta.setObjectName("Caption")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)

        self.strip_title = QLabel("")
        self.strip_title.setObjectName("Caption")
        self.strip_title.setVisible(False)
        layout.addWidget(self.strip_title)

        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.setSpacing(14)
        self.strip_layout.addStretch(1)
        self.strip.setVisible(False)
        layout.addWidget(self.strip)

        rows = QHBoxLayout()
        rows.setSpacing(8)
        rows.addStretch(1)
        self.reveal_btn = QPushButton(" 在资源管理器中显示")
        self.reveal_btn.setProperty("variant", "secondary")
        self.reveal_btn.setIcon(icons.icon("folder", theme.color("text_primary"), 16))
        self.reveal_btn.setIconSize(QSize(16, 16))
        self.reveal_btn.clicked.connect(self._reveal)
        close_btn = QPushButton("关闭")
        close_btn.setProperty("variant", "primary")
        close_btn.clicked.connect(self.accept)
        rows.addWidget(self.reveal_btn)
        rows.addWidget(close_btn)
        layout.addLayout(rows)

    # ----------------------------------------------------------- 加载 --
    def _load(self) -> None:
        path = self.item.path
        info = self.item.info or converter.probe(path)
        self.canvas.set_image(self._load_pixmap(path))

        parts = []
        if info.ok:
            parts.append(f"{info.width}×{info.height}")
            parts.append(info.format or path.suffix.lstrip(".").upper())
            parts.append(converter.human_size(info.bytes))
            if info.frames > 1:
                parts.append(f"{info.frames} 帧")
            parts.append("含透明通道" if info.has_alpha else "无透明通道")
        else:
            parts.append(info.message or "读取失败")
        if self.item.result is not None and self.item.result.ok:
            parts.append(f"已输出 → {self.item.result.dest.name}")
        self.meta.setText(" · ".join(parts))

        if path.suffix.lower() == ".ico":
            self._render_sizes(path)

    @staticmethod
    def _largest_ico_pixmap(path: Path) -> QPixmap | None:
        """Qt 读 .ico 只会拿到 16px 那帧，这里显式取最大尺寸。"""
        try:
            entries = ico.read_entries(path)
            size = max(int(entry["size"][0]) for entry in entries)
            from PIL import Image

            with Image.open(path) as img:
                frames = ico.render_frames(img, [size], fit="contain", upscale=False)
            if not frames:
                return None
            _, frame = frames[0]
            qimage = QImage(
                frame.tobytes("raw", "RGBA"),
                frame.width,
                frame.height,
                frame.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
            return QPixmap.fromImage(qimage)
        except Exception:  # noqa: BLE001
            return None

    def _load_pixmap(self, path: Path) -> QPixmap | None:
        if path.suffix.lower() == ".ico":
            pixmap = self._largest_ico_pixmap(path)
            if pixmap is not None:
                return pixmap
        pixmap = QPixmap(str(path))
        return None if pixmap.isNull() else pixmap

    def _render_sizes(self, path: Path) -> None:
        try:
            entries = ico.read_entries(path)
        except Exception:  # noqa: BLE001
            return
        sizes = sorted({int(entry["size"][0]) for entry in entries})
        if not sizes:
            return
        self.strip_title.setText(f"该图标包含 {len(sizes)} 个尺寸：")
        self.strip_title.setVisible(True)
        try:
            from PIL import Image

            with Image.open(path) as img:
                frames = ico.render_frames(img, sizes, fit="contain", upscale=False) if hasattr(ico, "render_frames") else []
        except Exception:  # noqa: BLE001
            frames = []
        cell = 92
        for size, frame in frames:
            box = QVBoxLayout()
            box.setSpacing(4)
            label = QLabel()
            label.setFixedSize(cell, cell)
            qimage = QImage(frame.tobytes("raw", "RGBA"), frame.width, frame.height,
                            frame.width * 4, QImage.Format.Format_RGBA8888).copy()
            pix = QPixmap.fromImage(qimage)
            zoom = 3 if size <= 24 else 2 if size <= 48 else 1
            target = min(cell, size * zoom)
            label.setPixmap(pix.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.FastTransformation))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = QLabel(f"{size}px" + (f" ×{zoom}" if zoom > 1 else ""))
            caption.setObjectName("Caption")
            caption.setFixedWidth(cell)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(caption, 0, Qt.AlignmentFlag.AlignHCenter)
            self.strip_layout.insertLayout(self.strip_layout.count() - 1, box)
        self.strip.setVisible(bool(frames))

    def _reveal(self) -> None:
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(self.item.path)])  # noqa: S603,S607
