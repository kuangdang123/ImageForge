"""分辨率放大页的输出设置面板。

与转换页一样，这里只做「token → 控件」和「控件 → UpscaleOptions」的映射，
模型/设备/预设等业务含义全部来自 imgforge.upscale。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
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
    PillOptionBox,
    SectionHeader,
    SegmentedControl,
    SwitchRow,
    repolish,
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

#: 安装卡里模型包的展示顺序与中文名；大小取 download.PACKS 里的声明。
PACK_ORDER = ("ncnn", "apisr")
PACK_LABELS = {
    "ncnn": "Real-CUGAN 模型",
    "apisr": "APISR 模型",
}


def _pack_size(key: str) -> float:
    pack = upscale.PACK_BY_KEY.get(key)
    size = getattr(pack, "size", 0) or 0
    return size / 1024 / 1024

#: 模型 tags -> 下拉框里的彩色短标签（文字, 角色）。
TAG_PILLS: dict[str, tuple[str, str]] = {
    "detail": ("画质", "warn"),
    "anime": ("动漫", "accent"),
    "slow": ("较慢", "muted"),
    "balanced": ("均衡", "accent"),
    "general": ("通用", "muted"),
    "fast": ("最快", "success"),
    "light": ("轻量", "muted"),
}


def model_pills(model) -> tuple[tuple[str, str], ...]:
    return tuple(TAG_PILLS[tag] for tag in getattr(model, "tags", ()) if tag in TAG_PILLS)


class ModelInstallWorker(QThread):
    """后台下载模型包，避免卡住 UI。"""

    progressChanged = Signal(int, int)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, pack_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pack_key = pack_key
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401 - QThread 入口
        target = Path(os.environ.get("IMAGEFORGE_MODELS_DIR") or upscale.default_models_root())
        try:
            installed = upscale.download_pack(
                self._pack_key,
                target,
                progress=lambda done, total: self.progressChanged.emit(done, total),
                cancel=lambda: self._cancelled,
            )
            self.succeeded.emit(str(target) + ("（" + "、".join(installed) + "）" if installed else ""))
        except upscale.DownloadCancelled:
            self.failed.emit("已取消下载")
        except upscale.DownloadError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 网络/解压任何异常都转成提示
            self.failed.emit(f"下载失败：{exc}")


class UpscalePanel(QWidget):
    changed = Signal()
    modelsChanged = Signal(bool)

    def __init__(self, theme: Theme, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self._building = True
        self._models = list(upscale.models_sorted())
        self._source_size: tuple[int, int] | None = None
        self._install_worker: ModelInstallWorker | None = None
        self._install_pack: str = ""

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

        self.install_card = self._build_install_card()
        self.column.addWidget(self.install_card)
        self.model_card = self._build_model_card()
        self.mode_card = self._build_mode_card()
        self.detail_card = self._build_detail_card()
        self.output_card = self._build_output_card()
        for card in (self.model_card, self.mode_card, self.detail_card, self.output_card):
            self.column.addWidget(card)
        self.column.addStretch(1)

        self._load()
        self._rebuild_scale_options()
        self._rebuild_denoise_options()
        self._building = False
        self._ready_state: bool | None = None
        self._sync_visibility()
        self._sync_install_state()

    # ------------------------------------------------------------- 构建 --
    def _build_install_card(self) -> Card:
        card = Card(self.theme, padding=16, spacing=10)
        header = SectionHeader(self.theme, "放大模型")
        header.set_hint("按需下载")
        card.body.addWidget(header)

        self.install_text = QLabel(
            "放大模型不随应用打包，第一次使用对应模型时再下载（下载一次，之后离线可用）。"
            "Real-CUGAN 走 Vulkan/CPU，APISR 走 DirectML GPU；也可以从文件导入 zip。"
        )
        self.install_text.setObjectName("Caption")
        self.install_text.setWordWrap(True)
        card.body.addWidget(self.install_text)

        self.install_rows_host = QWidget()
        self.install_rows = QVBoxLayout(self.install_rows_host)
        self.install_rows.setContentsMargins(0, 0, 0, 0)
        self.install_rows.setSpacing(tokens.SPACE["xs"])
        card.body.addWidget(self.install_rows_host)
        self._install_buttons: dict[str, tuple[QPushButton, QPushButton]] = {}

        self.install_progress = QProgressBar()
        self.install_progress.setObjectName("AppProgress")
        self.install_progress.setRange(0, 100)
        self.install_progress.setValue(0)
        self.install_progress.setFixedHeight(6)
        self.install_progress.setTextVisible(False)
        self.install_progress.setVisible(False)
        card.body.addWidget(self.install_progress)

        self.install_hint = QLabel("")
        self.install_hint.setObjectName("Caption")
        self.install_hint.setWordWrap(True)
        card.body.addWidget(self.install_hint)
        self._rebuild_install_rows()
        return card

    def _rebuild_install_rows(self) -> None:
        for button, _in_file in self._install_buttons.values():
            button.deleteLater()
            _in_file.deleteLater()
        self._install_buttons.clear()
        while self.install_rows.count():
            item = self.install_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for key in PACK_ORDER:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(tokens.SPACE["sm"])

            label = QLabel(f"{PACK_LABELS[key]} · 约 {_pack_size(key):.0f} MB")
            label.setProperty("role", "caption")
            layout.addWidget(label, 1)

            missing = not upscale.pack_ready(key)
            download = QPushButton("下载")
            download.setProperty("variant", "primary")
            download.setFixedHeight(28)
            download.setCursor(Qt.CursorShape.PointingHandCursor)
            download.setEnabled(missing)
            download.clicked.connect(lambda _checked=False, k=key: self._start_download(k))
            layout.addWidget(download)

            local = QPushButton("从文件…")
            local.setProperty("variant", "secondary")
            local.setFixedHeight(28)
            local.setCursor(Qt.CursorShape.PointingHandCursor)
            local.setEnabled(missing)
            local.clicked.connect(lambda _checked=False, k=key: self._install_from_file(k))
            layout.addWidget(local)

            if not missing:
                label.setText(label.text().replace(" · 约", " · 已安装 · 约"))
            self.install_rows.addWidget(row)
            self._install_buttons[key] = (download, local)

    def _build_model_card(self) -> Card:
        card = Card(self.theme, padding=14, spacing=10)
        header = SectionHeader(self.theme, "模型")
        header.set_hint("按性能排序")
        card.body.addWidget(header)

        options = [
            {
                "key": model.id,
                "label": model.name,
                "desc": model.description,
                "pills": model_pills(model),
            }
            for model in self._models
        ]
        self.model_box = PillOptionBox(self.theme, options, height=38)
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
        device_hint = QLabel("自动 = 优先 GPU（DirectML / Vulkan），不可用时回退 CPU")
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
        if not options:
            # ONNX（APISR）模型没有降噪档位，隐藏整行。
            self.denoise_label.setVisible(False)
            self.denoise_group.setVisible(False)
            return
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
        model = self.model
        is_onnx = model.backend == "onnx"
        scale = self._current_scale()
        if is_onnx:
            self.scale_hint.setText("APISR 固定 4× 输出，其它倍率会先在 4× 放大后缩放回目标尺寸")
            hint_visible = scale_mode and scale != 4
        else:
            self.scale_hint.setText("8× 及以上由多轮模型放大组合，耗时成倍增加")
            hint_visible = scale_mode and scale > 4
        self.scale_hint.setVisible(hint_visible)
        for widget in (self.preset_label, self.preset_group):
            widget.setVisible(not scale_mode)
        self.custom_row.setVisible(not scale_mode and self.preset_group.value() == CUSTOM)
        self.denoise_label.setVisible(not is_onnx)
        self.denoise_group.setVisible(not is_onnx)
        self.tta_row.setVisible(not is_onnx)
        note = model.description
        if not upscale.model_ready(model.id):
            pack = upscale.PACK_BY_KEY.get(model.pack)
            size = (getattr(pack, "size", 0) or 0) / 1024 / 1024
            note = f"{note}（需先下载 {PACK_LABELS.get(model.pack, '模型包')}，约 {size:.0f} MB）"
        self.model_note.setText(note)
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

    # ------------------------------------------------------- 模型安装 --
    def is_ready(self) -> bool:
        """任意一个模型包已安装（放大功能整体可用）。"""
        return any(upscale.pack_ready(key) for key in PACK_ORDER)

    def selected_ready(self) -> bool:
        """当前下拉框选中的模型所需的模型包是否就绪。"""
        return upscale.model_ready(self.model_box.value() or upscale.DEFAULT_MODEL_ID)

    def _sync_install_state(self) -> None:
        ready = self.is_ready()
        self.install_card.setVisible(not ready)
        for card in (self.model_card, self.mode_card, self.detail_card, self.output_card):
            card.setVisible(ready)
        if not ready:
            self.install_hint.setText(f"安装位置：{upscale.models_location_hint()}")
        elif self._install_worker is None:
            self.install_hint.setText("")
        states = tuple(upscale.pack_ready(key) for key in PACK_ORDER)
        if states != self._ready_state:
            self._ready_state = states
            self._rebuild_install_rows()
            self.modelsChanged.emit(ready)
        self._refresh_install_buttons()

    def _refresh_install_buttons(self) -> None:
        busy = self._install_worker is not None
        for key, (download, local) in self._install_buttons.items():
            missing = not upscale.pack_ready(key)
            download.setEnabled(missing and not busy)
            local.setEnabled(missing and not busy)

    def _start_download(self, pack_key: str) -> None:
        if self._install_worker is not None:
            return
        self._install_pack = pack_key
        self._refresh_install_buttons()
        self.install_progress.setVisible(True)
        self.install_progress.setValue(0)
        self.install_hint.setText(f"正在下载 {PACK_LABELS.get(pack_key, pack_key)}…")
        worker = ModelInstallWorker(pack_key, self)
        worker.progressChanged.connect(self._on_download_progress)
        worker.succeeded.connect(self._on_download_done)
        worker.failed.connect(self._on_download_failed)
        self._install_worker = worker
        worker.start()

    def _on_download_progress(self, done: int, total: int) -> None:
        if not total:
            total = int(_pack_size(self._install_pack) * 1024 * 1024)
        total = total or 1
        self.install_progress.setValue(int(done / total * 100))
        self.install_hint.setText(f"正在下载 {done / 1048576:.1f} / {total / 1048576:.1f} MB")

    def _on_download_done(self, message: str) -> None:
        self._finish_install(f"模型已安装：{message}")

    def _on_download_failed(self, message: str) -> None:
        self._install_worker = None
        self.install_progress.setVisible(False)
        self.install_hint.setText(message)
        self._refresh_install_buttons()

    def _install_from_file(self, pack_key: str | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模型包", "", "Zip 压缩包 (*.zip)")
        if not path:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            installed = upscale.install_archive(Path(path))
        except upscale.DownloadError as exc:
            self.install_hint.setText(str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        names = "、".join(PACK_LABELS.get(key, key) for key in installed) or "模型"
        self._finish_install(f"{names} 已安装")
        if pack_key and not upscale.pack_ready(pack_key):
            self.install_hint.setText("该 zip 里没有所选模型的包，请在右侧手动下载")

    def _finish_install(self, message: str) -> None:
        self._install_worker = None
        self.install_progress.setVisible(False)
        self.install_hint.setText(message)
        self._sync_install_state()
        self._sync_visibility()
        self.changed.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._install_worker is not None:
            self._install_worker.cancel()
            self._install_worker.wait(3000)
        super().closeEvent(event)

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
        self._sync_install_state()
        self._sync_visibility()

    def options(self) -> upscale.UpscaleOptions:
        mode = self.mode.value() or "scale"
        preset = self.preset_group.value() or "1080p"
        denoise = self.denoise_group.value()
        model = self.model
        return upscale.UpscaleOptions(
            model_id=self.model_box.value() or upscale.DEFAULT_MODEL_ID,
            device=self.device.value() or "auto",
            mode=mode,
            scale=self._current_scale(),
            preset=preset,
            custom_width=int(self.custom_w.value()),
            custom_height=int(self.custom_h.value()),
            denoise=int(denoise) if denoise not in (None, "") else None,
            tta=bool(self.tta_row.value()) and model.backend != "onnx",
            output_format=self.format_group.value() or "keep",
        )
