"""右侧输出设置面板：格式 / 质量 / ICO 细节 / 其它开关。

面板只负责“把 token 变成控件”和“把控件状态映射回 ConvertOptions”，
不做转换，也不碰文件系统。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import converter
from ..settings import Settings
from . import tokens
from .theme import Theme
from .widgets import (
    Card,
    ChipGroup,
    SectionHeader,
    SegmentedControl,
    SliderRow,
    SwitchRow,
    ToggleGrid,
)

FIT_OPTIONS = [("contain", "留白"), ("fill", "填充裁切"), ("stretch", "拉伸")]
PAYLOAD_OPTIONS = [("png", "PNG 压缩"), ("bmp", "BMP 兼容")]
NOTE_OVERRIDES = {
    "ico": "多尺寸 Windows 图标，可用作应用图标或 favicon",
    "png": "无损压缩，保留透明通道，适合图标与截图",
    "jpg": "有损压缩，体积最小，适合照片",
    "webp": "压缩率高，支持透明与动图",
    "bmp": "无压缩位图，兼容老系统与部分工业软件",
    "tiff": "无损，印刷与归档常用",
    "avif": "新一代压缩，体积最小但编码较慢",
}


class SettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, theme: Theme, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self._building = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACE["sm"])

        title = QLabel("输出设置")
        title.setFont(theme.font("h2"))
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        content = QWidget()
        self.column = QVBoxLayout(content)
        self.column.setContentsMargins(0, 0, 6, 0)
        self.column.setSpacing(10)
        scroll.setWidget(content)

        self.column.addWidget(self._build_format_card())
        self.column.addWidget(self._build_icon_card())
        self.column.addWidget(self._build_misc_card())
        self.column.addStretch(1)

        self._load()
        self._building = False
        self._sync_visibility()

    # ------------------------------------------------------------- 构建 --
    def _build_format_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        header = SectionHeader(self.theme, "输出格式")
        card.body.addWidget(header)
        options = [(key, converter.FORMATS[key].label) for key in converter.FORMAT_ORDER]
        self.target_group = ChipGroup(self.theme, options, columns=4)
        self.target_group.valueChanged.connect(self._on_target_changed)
        card.body.addWidget(self.target_group)
        self.note_label = QLabel("")
        self.note_label.setObjectName("Caption")
        self.note_label.setWordWrap(True)
        card.body.addWidget(self.note_label)
        self.quality_row = SliderRow(self.theme, "质量")
        self.quality_row.valueChanged.connect(self._on_quality_changed)
        card.body.addWidget(self.quality_row)
        return card

    def _build_icon_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        header = SectionHeader(self.theme, "图标细节")
        header.set_hint("仅 ICO 输出")
        self.icon_card = card
        self.icon_header = header
        card.body.addWidget(header)

        label = QLabel("尺寸")
        label.setObjectName("Caption")
        card.body.addWidget(label)
        self.sizes_grid = ToggleGrid(self.theme, [16, 24, 32, 48, 64, 128, 256], columns=4)
        self.sizes_grid.changed.connect(lambda _v: self._emit())
        card.body.addWidget(self.sizes_grid)

        fit_row = QWidget()
        fit_layout = QHBoxLayout(fit_row)
        fit_layout.setContentsMargins(0, 0, 0, 0)
        fit_layout.setSpacing(tokens.SPACE["sm"])
        fit_label = QLabel("非正方形")
        fit_label.setObjectName("Caption")
        fit_label.setFixedWidth(56)
        self.fit_control = SegmentedControl(self.theme, FIT_OPTIONS, height=30)
        self.fit_control.valueChanged.connect(lambda _v: self._emit())
        fit_layout.addWidget(fit_label)
        fit_layout.addWidget(self.fit_control, 1)
        card.body.addWidget(fit_row)

        payload_row = QWidget()
        payload_layout = QHBoxLayout(payload_row)
        payload_layout.setContentsMargins(0, 0, 0, 0)
        payload_layout.setSpacing(tokens.SPACE["sm"])
        payload_label = QLabel("像素数据")
        payload_label.setObjectName("Caption")
        payload_label.setFixedWidth(56)
        self.payload_control = SegmentedControl(self.theme, PAYLOAD_OPTIONS, height=30)
        self.payload_control.valueChanged.connect(lambda _v: self._emit())
        payload_layout.addWidget(payload_label)
        payload_layout.addWidget(self.payload_control, 1)
        card.body.addWidget(payload_row)
        return card

    def _build_misc_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=8)
        card.body.addWidget(SectionHeader(self.theme, "其它"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.exif_row = SwitchRow(self.theme, "保留 EXIF", "")
        self.upscale_row = SwitchRow(self.theme, "允许放大", "")
        self.recursive_row = SwitchRow(self.theme, "递归子目录", "")
        self.keep_tree_row = SwitchRow(self.theme, "保留结构", "")
        for row in (self.exif_row, self.upscale_row, self.recursive_row, self.keep_tree_row):
            row.toggled.connect(lambda _v: self._emit())
        grid.addWidget(self.exif_row, 0, 0)
        grid.addWidget(self.upscale_row, 0, 1)
        grid.addWidget(self.recursive_row, 1, 0)
        grid.addWidget(self.keep_tree_row, 1, 1)
        card.body.addLayout(grid)
        tips = {
            self.exif_row: "在 JPG / TIFF / WebP 里保留 EXIF 与 ICC 信息",
            self.upscale_row: "源图比目标尺寸小时仍放大生成（否则跳过该档）",
            self.recursive_row: "从文件夹添加时把子文件夹里的图片也列出来",
            self.keep_tree_row: "输出到指定文件夹时按原目录层级放置",
        }
        for row, tip in tips.items():
            row.setToolTip(tip)
            row.title.setToolTip(tip)
        return card

    # ------------------------------------------------------------- 数据 --
    def _load(self) -> None:
        s = self.settings
        target = str(s["target"])
        if target not in converter.FORMATS:
            target = "ico"
        self.target_group.set_value(target)
        self._apply_quality_default(target)
        self.sizes_grid.set_value(s.sizes())
        self.fit_control.set_value(str(s["ico_fit"]))
        self.payload_control.set_value(str(s["ico_payload"]))
        self.exif_row.set_value(bool(s["keep_metadata"]))
        self.upscale_row.set_value(bool(s["ico_upscale"]))
        self.recursive_row.set_value(bool(s["recursive"]))
        self.keep_tree_row.set_value(bool(s["keep_tree"]))

    def _apply_quality_default(self, target: str, value: int | None = None) -> None:
        spec = converter.FORMATS[target]
        quality = value if value is not None else self.settings.quality(target, spec.default_quality)
        self.quality_row.set_value(quality)

    def _emit(self) -> None:
        if self._building:
            return
        self.commit()

    def commit(self) -> None:
        """把当前控件状态写回 settings，并通知外部（主窗据此延迟落盘）。"""
        self._persist()
        self._sync_visibility()
        self.changed.emit()

    def _persist(self) -> None:
        s = self.settings
        target = self.target_group.value() or "ico"
        s["target"] = target
        if converter.FORMATS[target].kind == "lossy":
            s.set_quality(target, self.quality_row.value())
        s["ico_sizes"] = self.sizes_grid.value()
        s["ico_fit"] = self.fit_control.value()
        s["ico_payload"] = self.payload_control.value()
        s["keep_metadata"] = self.exif_row.value()
        s["ico_upscale"] = self.upscale_row.value()
        s["recursive"] = self.recursive_row.value()
        s["keep_tree"] = self.keep_tree_row.value()

    # -------------------------------------------------------- 交互回调 --
    def _on_target_changed(self, target: str) -> None:
        self._apply_quality_default(target)
        self._emit()

    def _on_quality_changed(self, value: int) -> None:
        if self._building:
            return
        target = self.target_group.value()
        if converter.FORMATS[target].kind == "lossy":
            self.settings.set_quality(target, value)
        self._emit()

    def _sync_visibility(self) -> None:
        target = self.target_group.value() or "ico"
        spec = converter.FORMATS[target]
        self.quality_row.setVisible(spec.kind == "lossy")
        self.icon_card.setVisible(target == "ico")
        self.note_label.setText(NOTE_OVERRIDES.get(target, f"{spec.note} · 输出 {spec.ext}"))
        self.keep_tree_row.setEnabled(self.settings["out_mode"] == "folder")

    # ---------------------------------------------------------- 对外 --
    def set_enabled(self, enabled: bool) -> None:
        self.target_group.set_enabled(enabled)
        self.sizes_grid.set_enabled(enabled)
        self.quality_row.slider.setEnabled(enabled)
        for row in (self.exif_row, self.upscale_row, self.recursive_row, self.keep_tree_row):
            row.setEnabled(enabled)
        self.fit_control.setEnabled(enabled)
        self.payload_control.setEnabled(enabled)

    def refresh_out_mode(self) -> None:
        self._sync_visibility()

    def options(self) -> converter.ConvertOptions:
        target = self.target_group.value() or "ico"
        spec = converter.FORMATS[target]
        quality = self.settings.quality(target, spec.default_quality)
        return converter.ConvertOptions(
            target=target,
            quality=int(self.quality_row.value() if spec.kind == "lossy" else quality),
            lossless=False,
            ico_sizes=tuple(self.sizes_grid.value()) or (16, 32, 48, 256),
            ico_fit=self.fit_control.value() or "contain",
            ico_payload=self.payload_control.value() or "png",
            ico_upscale=self.upscale_row.value(),
            keep_metadata=self.exif_row.value(),
        )

