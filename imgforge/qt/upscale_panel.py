"""分辨率放大页的输出设置面板。

与转换页一样，这里只做「token → 控件」和「控件 → UpscaleOptions」的映射，
模型/设备/预设等业务含义全部来自 imgforge.upscale。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import upscale
from ..settings import Settings
from . import tokens
from .theme import Theme
from .widgets import (
    Card,
    ChipGroup,
    SectionHeader,
    SegmentedControl,
    SelectBox,
    SwitchRow,
)

FORMAT_OPTIONS = [
    ("keep", "保持原格式"),
    ("png", "PNG"),
    ("jpg", "JPG"),
    ("webp", "WebP"),
]
MODE_OPTIONS = [("scale", "按倍数"), ("target", "按目标尺寸")]
DEVICE_OPTIONS = [("auto", "自动"), ("gpu", "GPU"), ("cpu", "CPU")]
CUSTOM = "custom"


class UpscalePanel(QWidget):
    changed = Signal()

    def __init__(self, theme: Theme, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self._building = True
        self._models = list(upscale.models_sorted())
        self._source_size: tuple[int, int] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACE["md"])

        title = QLabel("放大设置")
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
        self.column.setSpacing(tokens.SPACE["sm"])
        scroll.setWidget(content)

        self.column.addWidget(self._build_model_card())
        self.column.addWidget(self._build_mode_card())
        self.column.addWidget(self._build_detail_card())
        self.column.addWidget(self._build_output_card())
        self.column.addStretch(1)

        self._load()
        self._rebuild_scale_options()
        self._rebuild_denoise_options()
        self._building = False
        self._sync_visibility()

    # ------------------------------------------------------------- 构建 --
    def _build_model_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        header = SectionHeader(self.theme, "模型")
        header.set_hint("按性能排序")
        card.body.addWidget(header)

        options = [(model.id, model.name) for model in self._models]
        self.model_box = SelectBox(self.theme, options, height=36)
        self.model_box.valueChanged.connect(self._on_model_changed)
        card.body.addWidget(self.model_box)

        self.model_note = QLabel("")
        self.model_note.setObjectName("Caption")
        self.model_note.setWordWrap(True)
        card.body.addWidget(self.model_note)

        device_label = QLabel("设备")
        device_label.setObjectName("Caption")
        card.body.addWidget(device_label)
        self.device = SegmentedControl(self.theme, DEVICE_OPTIONS, height=30)
        self.device.valueChanged.connect(lambda _v: self._emit())
        card.body.addWidget(self.device)
        device_hint = QLabel("自动 = 优先 GPU（Vulkan），不可用时回退 CPU")
        device_hint.setObjectName("Caption")
        device_hint.setWordWrap(True)
        card.body.addWidget(device_hint)
        return card

    def _build_mode_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        card.body.addWidget(SectionHeader(self.theme, "放大方式"))
        self.mode = SegmentedControl(self.theme, MODE_OPTIONS, height=30)
        self.mode.valueChanged.connect(self._on_mode_changed)
        card.body.addWidget(self.mode)

        self.scale_label = QLabel("倍数")
        self.scale_label.setObjectName("Caption")
        card.body.addWidget(self.scale_label)
        self.scale_group = ChipGroup(self.theme, [], columns=4, compact=True)
        self.scale_group.valueChanged.connect(self._on_scale_changed)
        card.body.addWidget(self.scale_group)

        self.scale_hint = QLabel("8× 及以上由多轮模型放大组合，耗时成倍增加")
        self.scale_hint.setObjectName("Caption")
        self.scale_hint.setWordWrap(True)
        card.body.addWidget(self.scale_hint)

        self.preset_label = QLabel("目标尺寸")
        self.preset_label.setObjectName("Caption")
        card.body.addWidget(self.preset_label)
        preset_options = [(key, upscale.PRESET_LABELS[key]) for key in upscale.PRESET_ORDER]
        preset_options.append((CUSTOM, "自定义"))
        self.preset_group = ChipGroup(self.theme, preset_options, columns=3)
        self.preset_group.valueChanged.connect(self._on_preset_changed)
        card.body.addWidget(self.preset_group)

        self.custom_row = QWidget()
        custom_layout = QHBoxLayout(self.custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(tokens.SPACE["sm"])
        self.custom_w = QSpinBox()
        self.custom_h = QSpinBox()
        for spin, value in ((self.custom_w, 1920), (self.custom_h, 1080)):
            spin.setRange(16, 16384)
            spin.setValue(value)
            spin.setSingleStep(1)
            spin.setFixedHeight(30)
            spin.valueChanged.connect(lambda _v: self._on_custom_changed())
        sep = QLabel("×")
        sep.setObjectName("Caption")
        custom_layout.addWidget(self.custom_w, 1)
        custom_layout.addWidget(sep)
        custom_layout.addWidget(self.custom_h, 1)
        card.body.addWidget(self.custom_row)
        return card

    def _build_detail_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        card.body.addWidget(SectionHeader(self.theme, "细节"))
        self.denoise_label = QLabel("降噪")
        self.denoise_label.setObjectName("Caption")
        card.body.addWidget(self.denoise_label)
        self.denoise_group = ChipGroup(self.theme, [], columns=3, compact=True)
        self.denoise_group.valueChanged.connect(lambda _v: self._emit())
        card.body.addWidget(self.denoise_group)

        self.tta_row = SwitchRow(self.theme, "TTA 增强", "质量略升，速度约慢一倍")
        self.tta_row.toggled.connect(lambda _v: self._emit())
        card.body.addWidget(self.tta_row)

        self.estimate_label = QLabel("")
        self.estimate_label.setObjectName("Caption")
        card.body.addWidget(self.estimate_label)
        return card

    def _build_output_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        card.body.addWidget(SectionHeader(self.theme, "输出"))
        self.format_group = ChipGroup(self.theme, FORMAT_OPTIONS, columns=2, compact=True)
        self.format_group.valueChanged.connect(lambda _v: self._emit())
        card.body.addWidget(self.format_group)
        return card

    # ------------------------------------------------------------- 数据 --
    def _load(self) -> None:
        s = self.settings
        model_id = str(s["upscale_model"] or upscale.DEFAULT_MODEL_ID)
        if model_id not in upscale.MODEL_BY_ID:
            model_id = upscale.DEFAULT_MODEL_ID
        self.model_box.set_value(model_id)
        self.device.set_value(str(s["upscale_device"] or "auto"))
        self.mode.set_value(str(s["upscale_mode"] or "scale"))
        self.preset_group.set_value(str(s["upscale_preset"] or "1080p"))
        self.custom_w.setValue(int(s["upscale_custom_w"] or 1920))
        self.custom_h.setValue(int(s["upscale_custom_h"] or 1080))
        self.tta_row.set_value(bool(s["upscale_tta"]))
        self.format_group.set_value(str(s["upscale_format"] or "keep"))

    @property
    def model(self):
        return upscale.MODEL_BY_ID[self.model_box.value() or upscale.DEFAULT_MODEL_ID]

    def _current_scale(self) -> int:
        model = self.model
        try:
            scale = int(self.scale_group.value() or model.default_scale)
        except (TypeError, ValueError):
            scale = model.default_scale
        return max(1, scale)

    def _denoise_base_scale(self) -> int:
        """降噪档位按"单轮真实倍率"查表；组合倍率取模型最大单轮倍率。"""
        model = self.model
        scale = self._current_scale()
        scales = upscale.available_scales(model)
        if not scales:
            return max(2, model.default_scale)
        if scale in scales:
            return scale
        smaller = [item for item in scales if item <= scale]
        return smaller[-1] if smaller else scales[0]

    def _rebuild_scale_options(self, keep: int | None = None) -> None:
        model = self.model
        choices = list(upscale.SCALE_CHOICES)
        current = keep or int(self.settings["upscale_scale"] or model.default_scale)
        if current not in choices:
            choices.append(current)
            choices.sort()
        options = [(str(scale), f"{scale}×") for scale in choices]
        self.scale_group.set_options(options)
        if current not in choices:
            current = model.default_scale if model.default_scale in choices else choices[0]
        self.scale_group.set_value(str(current))

    def _rebuild_denoise_options(self, keep: int | None = None) -> None:
        model = self.model
        scale = self._denoise_base_scale()
        levels = model.denoise_levels_for(scale)
        current = keep if keep is not None else self.settings["upscale_denoise"]
        if current is None:
            current = model.default_denoise
        options = [(str(level), upscale.DENOISE_LABELS[level]) for level in levels]
        self.denoise_group.set_options(options)
        if current not in levels:
            current = levels[0]
        self.denoise_group.set_value(str(current))

    def _on_model_changed(self, model_id: str) -> None:
        self._rebuild_scale_options()
        self._rebuild_denoise_options(keep=None)
        self._sync_visibility()
        self._emit()

    def _on_scale_changed(self, _value: str) -> None:
        self._rebuild_denoise_options(keep=None)
        self._sync_visibility()
        self._emit()

    def _on_preset_changed(self, _value: str) -> None:
        self._sync_visibility()
        self._emit()

    def _on_custom_changed(self) -> None:
        if self._building:
            return
        self._update_estimate()
        self._emit()

    def _on_mode_changed(self, _value: str) -> None:
        self._sync_visibility()
        self._emit()

    def _sync_visibility(self) -> None:
        mode = self.mode.value()
        scale_mode = mode == "scale"
        for widget in (self.scale_label, self.scale_group, self.scale_hint):
            widget.setVisible(scale_mode)
        self.scale_hint.setVisible(scale_mode and self._current_scale() > 4)
        for widget in (self.preset_label, self.preset_group):
            widget.setVisible(not scale_mode)
        self.custom_row.setVisible(not scale_mode and self.preset_group.value() == CUSTOM)
        model = self.model
        self.model_note.setText(model.description)
        self.estimate_label.setText(self._estimate_text())

    def _estimate_text(self) -> str:
        if self._source_size is None:
            return "添加图片后显示预估尺寸"
        options = self.options()
        try:
            width, height = upscale.estimate_output_size(*self._source_size, options)
            mode = "目标" if options.mode == "target" else "倍数"
            return f"预估输出 {width} × {height}（{mode}）"
        except Exception:  # noqa: BLE001 - 预估失败不阻塞 UI
            return "预估输出：—"

    def set_source_size(self, width: int, height: int) -> None:
        self._source_size = (width, height)
        self.estimate_label.setText(self._estimate_text())

    # ------------------------------------------------------------- 交互 --
    def _emit(self) -> None:
        if self._building:
            return
        self._persist()
        self._update_estimate()
        self.changed.emit()

    def _update_estimate(self) -> None:
        self.estimate_label.setText(self._estimate_text())

    def _persist(self) -> None:
        s = self.settings
        s["upscale_model"] = self.model_box.value() or upscale.DEFAULT_MODEL_ID
        s["upscale_device"] = self.device.value() or "auto"
        s["upscale_mode"] = self.mode.value() or "scale"
        try:
            s["upscale_scale"] = self._current_scale()
        except (TypeError, ValueError):
            s["upscale_scale"] = self.model.default_scale
        s["upscale_preset"] = self.preset_group.value() or "1080p"
        s["upscale_custom_w"] = int(self.custom_w.value())
        s["upscale_custom_h"] = int(self.custom_h.value())
        try:
            s["upscale_denoise"] = int(self.denoise_group.value())
        except (TypeError, ValueError):
            s["upscale_denoise"] = self.model.default_denoise
        s["upscale_tta"] = self.tta_row.value()
        s["upscale_format"] = self.format_group.value() or "keep"

    # ------------------------------------------------------------- 对外 --
    def set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.model_box,
            self.device,
            self.mode,
            self.scale_group,
            self.preset_group,
            self.denoise_group,
            self.format_group,
        ):
            widget.setEnabled(enabled)
        self.tta_row.setEnabled(enabled)
        self.custom_w.setEnabled(enabled)
        self.custom_h.setEnabled(enabled)

    def refresh(self) -> None:
        self._sync_visibility()

    def options(self) -> upscale.UpscaleOptions:
        mode = self.mode.value() or "scale"
        preset = self.preset_group.value() or "1080p"
        denoise = self.denoise_group.value()
        return upscale.UpscaleOptions(
            model_id=self.model_box.value() or upscale.DEFAULT_MODEL_ID,
            device=self.device.value() or "auto",
            mode=mode,
            scale=self._current_scale(),
            preset=preset,
            custom_width=int(self.custom_w.value()),
            custom_height=int(self.custom_h.value()),
            denoise=int(denoise) if denoise not in (None, "") else None,
            tta=self.tta_row.value(),
            output_format=self.format_group.value() or "keep",
        )
