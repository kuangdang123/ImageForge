"""视频设置面板：输出格式 / 画面 / GIF 细节 / 输出位置。

和图片页的设置面板一样，只负责「token → 控件」与「控件 → VideoOptions」，
真正调用 ffmpeg 的是 `imgforge.video`。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import video
from ..settings import Settings
from . import tokens
from .theme import Theme
from .widgets import (
    Card,
    ChipGroup,
    ElidedLabel,
    SectionHeader,
    SegmentedControl,
    SliderRow,
    Switch,
    SwitchRow,
)

RESOLUTION_OPTIONS = [
    ("source", "原始"),
    ("720p", "720p"),
    ("1080p", "1080p"),
    ("1440p", "2K"),
    ("2160p", "4K"),
]
FPS_OPTIONS = [(str(value), f"{value}") for value in (10, 12, 15, 20, 24, 30)]
SIZE_OPTIONS = [("0", "原画"), ("20", "20 MB"), ("10", "10 MB"), ("5", "5 MB"), ("2", "2 MB"), ("custom", "自定义")]
SIZE_PRESETS_MB = (0, 20, 10, 5, 2)
OUT_OPTIONS = [("same", "源文件旁"), ("folder", "指定文件夹")]
NOTE_OVERRIDES = {
    "mp4": "兼容性最好，H.264 + AAC，适合分享与播放",
    "mkv": "封装灵活，适合保留多轨与高质量片源",
    "mov": "苹果生态常用，H.264 + AAC",
    "webm": "WebM 容器，VP9 + Opus，网页友好",
    "avi": "老格式，兼容旧播放器与部分工业设备",
    "gif": "无声循环动图，适合表情包与演示片段",
}


class VideoPanel(QWidget):
    """右侧视频设置；状态写回 settings，落盘由主窗去抖完成。"""

    changed = Signal()
    pick_dir_clicked = Signal()

    def __init__(self, theme: Theme, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self._building = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACE["sm"])

        title = QLabel("视频设置")
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
        self.column.setSpacing(8)
        scroll.setWidget(content)

        self.column.addWidget(self._build_format_card())
        self.column.addWidget(self._build_picture_card())
        self.column.addWidget(self._build_output_card())
        self.column.addStretch(1)

        self._load()
        self._building = False
        self._sync_visibility()

    # ------------------------------------------------------------- 构建 --
    def _build_format_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        card.body.addWidget(SectionHeader(self.theme, "输出格式"))
        options = [
            (key, video.VIDEO_FORMATS[key].label)
            for key in video.VIDEO_FORMAT_ORDER
            if key in video.VIDEO_FORMATS
        ]
        self.target_group = ChipGroup(self.theme, options, columns=3, compact=True)
        self.target_group.valueChanged.connect(self._on_target_changed)
        card.body.addWidget(self.target_group)
        self.note_label = QLabel("")
        self.note_label.setObjectName("Caption")
        self.note_label.setWordWrap(True)
        card.body.addWidget(self.note_label)
        self.quality_row = SliderRow(self.theme, "质量")
        self.quality_row.valueChanged.connect(lambda _v: self._emit())
        card.body.addWidget(self.quality_row)
        return card

    def _build_picture_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=8)
        card.body.addWidget(SectionHeader(self.theme, "画面"))
        label = QLabel("分辨率")
        label.setObjectName("Caption")
        card.body.addWidget(label)
        self.resolution_group = ChipGroup(self.theme, RESOLUTION_OPTIONS, columns=5, compact=True)
        self.resolution_group.valueChanged.connect(lambda _v: self._emit())
        card.body.addWidget(self.resolution_group)

        # ---- GIF 专属 ----
        self.gif_box = QWidget()
        gif_layout = QVBoxLayout(self.gif_box)
        gif_layout.setContentsMargins(0, 2, 0, 0)
        gif_layout.setSpacing(8)

        size_label = QLabel("体积上限")
        size_label.setObjectName("Caption")
        gif_layout.addWidget(size_label)
        self.size_group = ChipGroup(self.theme, SIZE_OPTIONS, columns=6, compact=True)
        self.size_group.valueChanged.connect(self._on_size_changed)
        gif_layout.addWidget(self.size_group)

        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(8)
        size_label.setToolTip("GIF 超过自动压缩到目标体积：优先保住分辨率（文字尽量清晰），再降帧率与颜色数，数值越大越清晰")
        self.max_mb_spin = QSpinBox()
        self.max_mb_spin.setRange(1, 2000)
        self.max_mb_spin.setSuffix(" MB")
        self.max_mb_spin.setFixedHeight(30)
        self.max_mb_spin.setFixedWidth(92)
        self.max_mb_spin.valueChanged.connect(self._on_size_value_changed)
        size_layout.addStretch(1)
        size_layout.addWidget(self.max_mb_spin)
        gif_layout.addWidget(size_row)

        fps_label = QLabel("帧率")
        fps_label.setObjectName("Caption")
        gif_layout.addWidget(fps_label)
        self.fps_group = ChipGroup(self.theme, FPS_OPTIONS, columns=6, compact=True)
        self.fps_group.valueChanged.connect(lambda _v: self._emit())
        gif_layout.addWidget(self.fps_group)

        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(8)
        start_box = QVBoxLayout()
        start_box.setSpacing(2)
        start_label = QLabel("起点")
        start_label.setObjectName("Caption")
        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, 86400)
        self.start_spin.setSuffix(" 秒")
        self.start_spin.setFixedHeight(30)
        self.start_spin.valueChanged.connect(lambda _v: self._emit())
        start_box.addWidget(start_label)
        start_box.addWidget(self.start_spin)
        duration_box = QVBoxLayout()
        duration_box.setSpacing(2)
        duration_label = QLabel("时长（0 = 整段）")
        duration_label.setObjectName("Caption")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 86400)
        self.duration_spin.setSuffix(" 秒")
        self.duration_spin.setFixedHeight(30)
        self.duration_spin.valueChanged.connect(lambda _v: self._emit())
        duration_box.addWidget(duration_label)
        duration_box.addWidget(self.duration_spin)
        range_layout.addLayout(start_box, 1)
        range_layout.addLayout(duration_box, 1)
        gif_layout.addWidget(range_row)

        loop_row = QWidget()
        loop_layout = QHBoxLayout(loop_row)
        loop_layout.setContentsMargins(0, 0, 0, 0)
        loop_layout.setSpacing(8)
        self.loop_label = QLabel("循环播放")
        self.loop_switch = Switch(self.theme)
        self.loop_switch.setChecked(True)
        self.loop_switch.toggled.connect(lambda _v: self._emit())
        loop_layout.addWidget(self.loop_label)
        loop_layout.addStretch(1)
        loop_layout.addWidget(self.loop_switch)
        gif_layout.addWidget(loop_row)
        card.body.addWidget(self.gif_box)

        self.audio_row = SwitchRow(self.theme, "保留音频", "非 GIF 输出时保留原音轨")
        self.audio_row.toggled.connect(lambda _v: self._emit())
        card.body.addWidget(self.audio_row)
        return card

    def _build_output_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=8)
        card.body.addWidget(SectionHeader(self.theme, "输出"))
        self.out_mode = SegmentedControl(self.theme, OUT_OPTIONS, height=30)
        self.out_mode.valueChanged.connect(self._on_out_mode_changed)
        card.body.addWidget(self.out_mode)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.out_label = ElidedLabel("", self)
        self.out_label.setObjectName("Caption")
        self.pick_btn = QPushButton("选择…")
        self.pick_btn.setProperty("variant", "secondary")
        self.pick_btn.setFixedHeight(28)
        self.pick_btn.clicked.connect(self.pick_dir_clicked.emit)
        layout.addWidget(self.out_label, 1)
        layout.addWidget(self.pick_btn)
        card.body.addWidget(row)
        return card

    # ------------------------------------------------------------- 数据 --
    def _load(self) -> None:
        s = self.settings
        target = str(s["video_target"])
        if target not in video.VIDEO_FORMATS:
            target = "mp4"
        self.target_group.set_value(target)
        self.quality_row.set_value(int(s["video_quality"]))
        resolution = str(s["video_resolution"])
        if resolution not in dict(RESOLUTION_OPTIONS):
            resolution = "source"
        self.resolution_group.set_value(resolution)
        fps = str(int(s["video_fps"]))
        if fps not in dict(FPS_OPTIONS):
            fps = "15"
        self.fps_group.set_value(fps)
        max_bytes = max(0, int(s["video_max_bytes"]))
        self.max_mb_spin.setValue(max(1, round(max_bytes / (1024 * 1024))) if max_bytes else 8)
        preset = "0" if not max_bytes else str(round(max_bytes / (1024 * 1024)))
        self.size_group.set_value(preset if preset in dict(SIZE_OPTIONS) else "custom")
        self.start_spin.setValue(int(s["video_start"]))
        self.duration_spin.setValue(int(s["video_duration"]))
        self.loop_switch.setChecked(bool(s["video_loop"]))
        self.audio_row.set_value(bool(s["video_audio"]))
        self.out_mode.set_value(str(s["out_mode"]))
        self._refresh_out_label()

    def _emit(self) -> None:
        if self._building:
            return
        self.commit()

    def commit(self) -> None:
        self._persist()
        self._sync_visibility()
        self.changed.emit()

    def _persist(self) -> None:
        s = self.settings
        s["video_target"] = self.target_group.value() or "mp4"
        s["video_quality"] = int(self.quality_row.value())
        s["video_resolution"] = self.resolution_group.value() or "source"
        s["video_fps"] = int(self.fps_group.value() or 15)
        s["video_start"] = int(self.start_spin.value())
        s["video_duration"] = int(self.duration_spin.value())
        s["video_loop"] = self.loop_switch.isChecked()
        s["video_audio"] = self.audio_row.value()
        s["video_max_bytes"] = self._max_bytes()
        s["out_mode"] = self.out_mode.value() or "same"

    # -------------------------------------------------------- 交互回调 --
    def _on_target_changed(self, target: str) -> None:
        self._sync_visibility()
        self._emit()

    def _max_bytes(self) -> int:
        value = self.size_group.value() or "0"
        if value == "0":
            return 0
        mb = self.max_mb_spin.value() if value == "custom" else int(value)
        return int(mb) * 1024 * 1024

    def _on_size_changed(self, value: str) -> None:
        if value not in ("0", "custom"):
            self.max_mb_spin.blockSignals(True)
            self.max_mb_spin.setValue(int(value))
            self.max_mb_spin.blockSignals(False)
        self._sync_visibility()
        self._emit()

    def _on_size_value_changed(self, value: int) -> None:
        # 手动改成与预设相同的值时，自动切到对应预设，避免下拉与数值打架。
        current = self.size_group.value() or "custom"
        if current != "custom" and int(current) != value:
            self.size_group.blockSignals(True)
            self.size_group.set_value("custom")
            self.size_group.blockSignals(False)
        self._sync_visibility()
        self._emit()

    def _on_out_mode_changed(self, mode: str) -> None:
        self._refresh_out_label()
        self._emit()

    def _refresh_out_label(self) -> None:
        folder = self.settings["out_dir"]
        if self.out_mode.value() == "folder":
            text = str(folder) or "还没有选择目录"
            self.out_label.setObjectName("Warning" if not folder else "")
            self.pick_btn.setVisible(True)
        else:
            text = "与源视频同目录"
            self.out_label.setObjectName("")
            self.pick_btn.setVisible(False)
        self.out_label.setText(text)
        self._repolish_label()

    def _repolish_label(self) -> None:
        from .widgets import repolish

        repolish(self.out_label)

    def _sync_visibility(self) -> None:
        target = self.target_group.value() or "mp4"
        spec = video.VIDEO_FORMATS[target]
        is_gif = target == "gif"
        self.quality_row.setVisible(not is_gif)
        self.gif_box.setVisible(is_gif)
        self.max_mb_spin.setVisible((self.size_group.value() or "0") == "custom")
        self.audio_row.setVisible(not is_gif)
        self.note_label.setText(NOTE_OVERRIDES.get(target, f"{spec.note} · 输出 {spec.ext}"))
        self._refresh_out_label()

    # ---------------------------------------------------------- 对外 --
    def set_enabled(self, enabled: bool) -> None:
        for group in (self.target_group, self.resolution_group, self.fps_group, self.size_group):
            group.set_enabled(enabled)
        self.max_mb_spin.setEnabled(enabled)
        self.quality_row.slider.setEnabled(enabled)
        self.audio_row.setEnabled(enabled)
        self.loop_switch.setEnabled(enabled)
        self.loop_label.setEnabled(enabled)
        self.start_spin.setEnabled(enabled)
        self.duration_spin.setEnabled(enabled)
        self.out_mode.setEnabled(enabled)
        self.pick_btn.setEnabled(enabled)

    def refresh(self) -> None:
        self._sync_visibility()

    def options(self) -> video.VideoOptions:
        spec = video.VIDEO_FORMATS[self.target_group.value() or "mp4"]
        return video.VideoOptions(
            target=spec.key,
            quality=int(self.quality_row.value() if spec.kind != "gif" else 100),
            resolution=self.resolution_group.value() or "source",
            fps=int(self.fps_group.value() or 15),
            start=int(self.start_spin.value()),
            duration=int(self.duration_spin.value()),
            loop=self.loop_switch.isChecked(),
            audio=self.audio_row.value(),
            overwrite=bool(self.settings["video_overwrite"]),
            max_bytes=self._max_bytes(),
        )
