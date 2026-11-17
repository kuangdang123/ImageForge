"""主窗口：命令栏 + 文件队列 + 输出设置 + 底栏。

布局参考 Fluent / DevToys 一类「卡片式工具」桌面应用：
- 顶部只有一条命令栏，主动作在左，输出目标在右；
- 主体是「队列卡片 + 设置面板」两栏，设置面板固定宽度、内部可滚动；
- 底栏承担进度与最终动作，转换中不跳动布局。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import converter, ico, upscale
from ..settings import Settings
from ..version import __version__
from . import icons
from .preview import PreviewDialog
from .queueview import FileModel, FilePanel, QueueItem, THUMB_SIZE
from .settings_panel import SettingsPanel
from .theme import Theme
from .upscale_panel import UpscalePanel
from .widgets import ElidedLabel, MiniProgress, SegmentedControl, Toast

ASSETS = Path(__file__).resolve().parent.parent / "assets"

OUT_MODE_OPTIONS = [("same", "源文件旁"), ("folder", "指定文件夹")]
MODE_OPTIONS = [("convert", "格式转换"), ("upscale", "分辨率放大")]
MODE_NOUNS = {"convert": ("转换", "待转换", "转换中", "已转换"), "upscale": ("放大", "待放大", "放大中", "已放大")}
THEME_CYCLE = ["system", "light", "dark"]
THEME_LABELS = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
THEME_ICONS = {"system": "monitor", "light": "sun", "dark": "moon"}


# --------------------------------------------------------------- 异步任务 --
class ProbeTask(QRunnable):
    """后台解码：读元信息 + 生成 80px 缩略图（QImage，主线程再转 QPixmap）。"""

    class Signals(QObject):
        done = Signal(int, str, object, object)
        failed = Signal(int, str, str)

    def __init__(self, row: int, path: Path) -> None:
        super().__init__()
        self.row = row
        self.path = path
        self.signals = ProbeTask.Signals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401
        info = converter.probe(self.path)
        if not info.ok:
            self.signals.failed.emit(self.row, str(self.path), info.message or "读取失败")
            return
        image = None
        try:
            from PIL import Image

            with Image.open(self.path) as img:
                if img.format in ("JPEG", "MPO"):
                    img.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
                img.thumbnail((THUMB_SIZE * 2, THUMB_SIZE * 2))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA" if info.has_alpha else "RGB")
                raw = img.tobytes("raw", "RGBA" if img.mode == "RGBA" else "RGB")
                fmt = QImage.Format.Format_RGBA8888 if img.mode == "RGBA" else QImage.Format.Format_RGB888
                stride = img.width * (4 if img.mode == "RGBA" else 3)
                image = QImage(raw, img.width, img.height, stride, fmt).copy()
        except Exception:  # noqa: BLE001 - 缩略图失败不影响条目
            image = None
        self.signals.done.emit(self.row, str(self.path), info, image)


class ConversionWorker(QThread):
    progressed = Signal(int, int, str)
    resulted = Signal(object)
    finished_batch = Signal(list, bool)

    def __init__(
        self,
        paths: list[Path],
        options: converter.ConvertOptions,
        out_root: Path | None,
        keep_tree: bool,
        base_dir: Path | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.options = options
        self.out_root = out_root
        self.keep_tree = keep_tree
        self.base_dir = base_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        results: list[converter.ConvertResult] = []
        total = len(self.paths)

        def on_result(index: int, result: converter.ConvertResult) -> None:
            results.append(result)
            self.resulted.emit(result)
            self.progressed.emit(index + 1, total, result.src.name)

        try:
            converter.run_batch(
                self.paths,
                self.options,
                self.out_root,
                keep_tree=self.keep_tree,
                base_dir=self.base_dir,
                on_result=on_result,
                should_cancel=lambda: self._cancelled,
            )
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默死亡
            results.append(
                converter.ConvertResult(src=self.paths[0] if self.paths else Path(""), message=f"内部错误：{exc}")
            )
        self.finished_batch.emit(results, self._cancelled)


class UpscaleWorker(QThread):
    """Real-CUGAN 批处理线程：逐张放大，进度/结果通过信号回主线程。"""

    progressed = Signal(int, int, str)
    resulted = Signal(object)
    finished_batch = Signal(list, bool)

    def __init__(
        self,
        paths: list[Path],
        options: upscale.UpscaleOptions,
        out_root: Path | None,
        keep_tree: bool,
        base_dir: Path | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.options = options
        self.out_root = out_root
        self.keep_tree = keep_tree
        self.base_dir = base_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        results: list[upscale.UpscaleResult] = []
        total = len(self.paths)

        def on_result(index: int, result: upscale.UpscaleResult) -> None:
            results.append(result)
            self.resulted.emit(result)
            self.progressed.emit(index + 1, total, result.src.name)

        try:
            upscale.run_batch(
                self.paths,
                self.options,
                self.out_root,
                keep_tree=self.keep_tree,
                base_dir=self.base_dir,
                on_result=on_result,
                should_cancel=lambda: self._cancelled,
            )
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默死亡
            results.append(upscale.UpscaleResult(src=self.paths[0] if self.paths else Path(""), message=f"内部错误：{exc}"))
        self.finished_batch.emit(results, self._cancelled)


# ------------------------------------------------------------------- 主窗 --
class MainWindow(QMainWindow):
    def __init__(self, theme: Theme, settings: Settings) -> None:
        super().__init__()
        self.theme = theme
        self.settings = settings
        self.model = FileModel(theme, self)
        self._worker: ConversionWorker | None = None
        self._upscale_worker: UpscaleWorker | None = None
        self.mode = str(settings["mode"] or "convert")
        self._probe_pool = QThreadPool(self)
        self._probe_pool.setMaxThreadCount(min(4, os.cpu_count() or 2))
        self._pending_probe = 0
        self._autostart = False
        self._convert_started = 0
        self._convert_done = 0
        self._convert_failed = 0
        self._last_output: Path | None = None
        # 设置项改动后延迟落盘：连续拖滑杆时不会反复写文件。
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._flush_settings)

        self.setWindowTitle("ImageForge · 图片格式转换与超分")
        self.setWindowIcon(QIcon(str(ASSETS / "app.ico")))
        self.setAcceptDrops(True)
        self.setMinimumSize(1040, 660)
        self._build_ui()
        self._bind_shortcuts()
        self._load_geometry()
        theme.changed.connect(self._on_theme_changed)
        self._update_theme_button()
        self._sync_state()

    # ------------------------------------------------------------- 构建 --
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        outer.addLayout(self._build_header())
        outer.addLayout(self._build_nav())
        outer.addLayout(self._build_command_bar())
        outer.addLayout(self._build_body(), 1)
        outer.addWidget(self._build_footer())

        self.toast = Toast(central, self.theme)

    def _build_nav(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.nav = SegmentedControl(self.theme, MODE_OPTIONS, height=34)
        self.nav.setFixedWidth(260)
        self.nav.valueChanged.connect(self._on_mode_changed)
        row.addWidget(self.nav)
        row.addStretch(1)
        return row

    def _build_header(self) -> QHBoxLayout:
        theme = self.theme
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(12)

        self.logo = QLabel()
        logo_path = ASSETS / "logo.png"
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                self.logo.setPixmap(
                    pix.scaled(
                        38,
                        38,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self.logo.setFixedSize(38, 38)
        row.addWidget(self.logo)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("ImageForge")
        title.setFont(theme.font("h1"))
        badge = QLabel(__version__)
        badge.setObjectName("VersionBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedHeight(20)
        title_row.addWidget(title)
        title_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        subtitle = QLabel("格式转换 · 多尺寸 ICO · 分辨率放大")
        subtitle.setObjectName("Caption")
        titles.addLayout(title_row)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch(1)

        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("IconButton")
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self._cycle_theme)
        row.addWidget(self.theme_btn)
        return row

    def _build_command_bar(self) -> QHBoxLayout:
        theme = self.theme
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(8)

        self.add_btn = QPushButton(" 添加图片")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.setIcon(icons.icon("plus", "#FFFFFF", 16))
        self.add_btn.clicked.connect(self.add_files)

        self.folder_btn = QPushButton(" 添加文件夹")
        self.folder_btn.setProperty("variant", "secondary")
        self.folder_btn.setIcon(icons.icon("folder", theme.color("text_primary"), 16))
        self.folder_btn.clicked.connect(self.choose_folder)

        self.paste_btn = QPushButton(" 粘贴")
        self.paste_btn.setProperty("variant", "ghost")
        self.paste_btn.setIcon(icons.icon("clipboard", theme.color("text_secondary"), 16))
        self.paste_btn.clicked.connect(self.paste)

        self.clear_btn = QPushButton(" 清空")
        self.clear_btn.setProperty("variant", "ghost")
        self.clear_btn.setIcon(icons.icon("trash", theme.color("text_secondary"), 16))
        self.clear_btn.clicked.connect(self.clear_all)

        for button in (self.add_btn, self.folder_btn, self.paste_btn):
            button.setIconSize(QSize(16, 16))
            bar.addWidget(button)
        bar.addStretch(1)

        out_label = QLabel("输出到")
        out_label.setObjectName("Caption")
        bar.addWidget(out_label)

        self.out_mode = SegmentedControl(theme, OUT_MODE_OPTIONS, height=32)
        self.out_mode.setFixedWidth(196)
        self.out_mode.valueChanged.connect(self._on_out_mode_changed)
        bar.addWidget(self.out_mode)

        self.out_dir_label = ElidedLabel("", mode=Qt.TextElideMode.ElideLeft)
        self.out_dir_label.setObjectName("Caption")
        self.out_dir_label.setMinimumWidth(120)
        self.out_dir_label.setMaximumWidth(260)
        bar.addWidget(self.out_dir_label)

        self.browse_btn = QToolButton()
        self.browse_btn.setObjectName("IconButton")
        self.browse_btn.setFixedSize(32, 32)
        self.browse_btn.setIcon(icons.icon("folder", theme.color("text_secondary"), 16))
        self.browse_btn.setToolTip("选择输出文件夹")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self.choose_output_dir)
        bar.addWidget(self.browse_btn)

        bar.addWidget(self.clear_btn)
        return bar

    def _build_body(self) -> QHBoxLayout:
        theme = self.theme
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.file_panel = FilePanel(theme)
        self.file_panel.add_clicked.connect(self.add_files)
        self.file_panel.remove_requested.connect(self.remove_row)
        self.file_panel.preview_requested.connect(self.preview_row)
        self.file_panel.list.setModel(self.model)
        body.addWidget(self.file_panel, 1)

        right = QWidget()
        right.setFixedWidth(356)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.panel_stack = QStackedWidget()
        self.settings_panel = SettingsPanel(theme, self.settings)
        self.settings_panel.changed.connect(self._on_options_changed)
        self.upscale_panel = UpscalePanel(theme, self.settings)
        self.upscale_panel.changed.connect(self._on_options_changed)
        self.upscale_panel.modelsChanged.connect(self._on_models_changed)
        self.panel_stack.addWidget(self.settings_panel)
        self.panel_stack.addWidget(self.upscale_panel)
        self.panel_stack.setCurrentWidget(self.settings_panel if self.mode == "convert" else self.upscale_panel)
        right_layout.addWidget(self.panel_stack)
        body.addWidget(right)
        return body

    def _build_footer(self) -> QWidget:
        theme = self.theme
        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(8)

        self.progress = MiniProgress(theme)
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.status_label = QLabel("拖入图片，或按 Ctrl+O 添加")
        self.status_label.setObjectName("Caption")
        row.addWidget(self.status_label)
        row.addStretch(1)

        self.open_dir_btn = QPushButton(" 打开输出目录")
        self.open_dir_btn.setProperty("variant", "ghost")
        self.open_dir_btn.setIcon(icons.icon("external", theme.color("text_secondary"), 16))
        self.open_dir_btn.setIconSize(QSize(16, 16))
        self.open_dir_btn.clicked.connect(self.open_output_dir)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setProperty("variant", "secondary")
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        self.cancel_btn.setEnabled(False)

        self.start_btn = QPushButton("开始转换")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setMinimumWidth(132)
        self.start_btn.clicked.connect(self.start_current)

        row.addWidget(self.open_dir_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.start_btn)
        layout.addLayout(row)
        return footer

    def _bind_shortcuts(self) -> None:
        for keys, slot in (
            ("Ctrl+O", self.add_files),
            ("Ctrl+Shift+O", self.choose_folder),
            ("Ctrl+V", self.paste),
            ("Ctrl+Return", self.start_current),
            ("Esc", self.cancel_conversion),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        self.nav.set_value(self.mode)
        self._refresh_mode_texts()

    # ------------------------------------------------------------- 主题 --
    def _cycle_theme(self) -> None:
        current = self.theme.mode
        index = THEME_CYCLE.index(current) if current in THEME_CYCLE else 0
        mode = THEME_CYCLE[(index + 1) % len(THEME_CYCLE)]
        self.theme.set_mode(mode)
        self.settings["theme"] = mode
        self.settings.save()
        self.toast.show_message(f"主题：{THEME_LABELS[mode]}", "info", 1600)

    def _update_theme_button(self) -> None:
        mode = self.theme.mode
        self.theme_btn.setIcon(icons.icon(THEME_ICONS.get(mode, "monitor"), self.theme.color("text_secondary"), 18))
        self.theme_btn.setToolTip(f"主题：{THEME_LABELS.get(mode, mode)}（点击切换）")

    def _on_theme_changed(self) -> None:
        self._update_theme_button()
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        theme = self.theme
        self.add_btn.setIcon(icons.icon("plus", "#FFFFFF", 16))
        self.folder_btn.setIcon(icons.icon("folder", theme.color("text_primary"), 16))
        self.paste_btn.setIcon(icons.icon("clipboard", theme.color("text_secondary"), 16))
        self.clear_btn.setIcon(icons.icon("trash", theme.color("text_secondary"), 16))
        self.browse_btn.setIcon(icons.icon("folder", theme.color("text_secondary"), 16))
        self.open_dir_btn.setIcon(icons.icon("external", theme.color("text_secondary"), 16))
        self._update_theme_button()
        self.model.refresh_all()
        self.file_panel.list.viewport().update()

    # ------------------------------------------------------------- 添加 --
    def add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(converter.INPUT_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片", self._start_dir(), f"图片文件 ({patterns});;所有文件 (*.*)")
        if paths:
            self.add_paths([Path(p) for p in paths])

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", self._start_dir())
        if not folder:
            return
        self._open_picker(Path(folder))

    def _open_picker(self, folder: Path) -> None:
        if self._is_busy():
            return
        try:
            from .picker import FolderPicker
        except Exception as exc:  # noqa: BLE001 - 挑图窗口不可用时不阻塞主流程
            self.toast.show_message(f"挑图窗口不可用：{exc}", "error", 3200)
            return
        picker = FolderPicker(self, self.theme, folder, existing=set(self.model.paths()), recursive=bool(self.settings["recursive"]))
        picker.exec()
        self.settings["recursive"] = bool(picker.recursive())
        self.settings["last_dir"] = str(folder)
        self.settings.save()
        if picker.selected_paths():
            self.add_paths(picker.selected_paths(), autostart=picker.start_requested())

    def paste(self) -> None:
        data = QApplication.clipboard().mimeData()
        if data.hasUrls():
            paths = [Path(url.toLocalFile()) for url in data.urls() if url.isLocalFile()]
            paths = [p for p in paths if p.suffix.lower() in converter.INPUT_EXTS]
            if paths:
                self.add_paths(paths)
                return
        image = QApplication.clipboard().image()
        if not image.isNull():
            tmp_dir = Path(tempfile.gettempdir()) / "ImageForge"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            dest = tmp_dir / f"paste_{os.getpid()}_{int(QApplication.applicationPid())}.png"
            index = 1
            while dest.exists():
                dest = tmp_dir / f"paste_{os.getpid()}_{index}.png"
                index += 1
            image.save(str(dest), "PNG")
            self.add_paths([dest])
            return
        self.toast.show_message("剪贴板里没有图片", "warning", 2000)

    def add_paths(self, paths: list[Path], *, autostart: bool = False) -> None:
        if self._is_busy():
            return
        rows = self.model.add_paths(paths)
        if not rows:
            self.toast.show_message("这些图片已经在列表里了", "info", 1800)
            return
        self.file_panel.show_list()
        for row in rows:
            self.model.set_status(row, "reading")
            self._schedule_probe(row, self.model.item(row))
        if autostart:
            self._autostart = True
        self._sync_state()
        self._refresh_idle_status()

    def _schedule_probe(self, row: int, item: QueueItem | None) -> None:
        if item is None:
            return
        self._pending_probe += 1
        task = ProbeTask(row, item.path)
        task.signals.done.connect(self._on_probe_done)
        task.signals.failed.connect(self._on_probe_failed)
        self._probe_pool.start(task)

    def _on_probe_done(self, row: int, path: str, info, image) -> None:
        self._pending_probe = max(0, self._pending_probe - 1)
        item = self.model.item(row)
        if item is None or str(item.path) != path:
            row2 = self.model.row_of(Path(path))
            if row2 is None:
                self._maybe_autostart()
                return
            row = row2
        thumb = QPixmap.fromImage(image) if image is not None else None
        self.model.set_info(row, info, thumb)
        if info is not None and getattr(info, "ok", False):
            self.upscale_panel.set_source_size(info.width, info.height)
        self.file_panel.set_counts(*self._counts())
        self._sync_state()
        if self._pending_probe == 0 and not self._is_busy():
            self._refresh_idle_status()
        self._maybe_autostart()

    def _on_probe_failed(self, row: int, path: str, message: str) -> None:
        self._pending_probe = max(0, self._pending_probe - 1)
        row2 = self.model.row_of(Path(path))
        if row2 is not None:
            self.model.set_status(row2, "failed", message=message)
        self.file_panel.set_counts(*self._counts())
        self._sync_state()
        if self._pending_probe == 0 and not self._is_busy():
            self._refresh_idle_status()
        self._maybe_autostart()

    def _maybe_autostart(self) -> None:
        if self._autostart and self._pending_probe == 0:
            self._autostart = False
            QTimer.singleShot(60, self.start_conversion)

    # ------------------------------------------------------------- 编辑 --
    def remove_row(self, row: int) -> None:
        if self._is_busy():
            return
        self.model.remove_rows([row])
        self._after_model_change()

    def clear_all(self) -> None:
        if self._is_busy():
            return
        if not self.model.items:
            return
        self.model.clear()
        self._after_model_change()
        self.toast.show_message("已清空列表", "info", 1500)

    def _after_model_change(self) -> None:
        total = len(self.model.items)
        if total:
            self.file_panel.show_list()
        else:
            self.file_panel.show_empty()
        self.file_panel.set_counts(*self._counts())
        self._sync_state()
        if not self._is_busy():
            self._refresh_idle_status()

    def _refresh_idle_status(self) -> None:
        items = self.model.items
        total = len(items)
        if self.mode == "upscale" and not self.upscale_panel.selected_ready():
            self.status_label.setText("所选放大模型未安装，先在右侧卡片里下载对应模型包")
            return
        verb, pending_word, running_word, done_word = MODE_NOUNS.get(self.mode, MODE_NOUNS["convert"])
        if total == 0:
            self.status_label.setText("拖入图片或文件夹，也可以按 Ctrl+O 添加")
            return
        if any(item.status in ("reading", "probing") for item in items):
            self.status_label.setText(f"正在读取信息… {total} 张")
            return
        pending = sum(1 for item in items if item.status in ("idle", "failed"))
        if pending:
            self.status_label.setText(f"共 {total} 张 · {pending} 张{pending_word}")
        else:
            self.status_label.setText(f"共 {total} 张 · {done_word}")

    def _counts(self) -> tuple[int, int, int]:
        counts = self.model.status_counts()
        return len(self.model.items), counts.get("done", 0), counts.get("failed", 0)

    def preview_row(self, row: int) -> None:
        item = self.model.item(row)
        if item is None:
            return
        dialog = PreviewDialog(self, self.theme, item)
        dialog.exec()

    # ------------------------------------------------------------- 输出 --
    def _start_dir(self) -> str:
        last = str(self.settings["last_dir"] or "")
        if last and Path(last).is_dir():
            return last
        return str(Path.home())

    def _on_out_mode_changed(self, mode: str) -> None:
        self.settings["out_mode"] = mode
        self.settings.save()
        self.settings_panel.refresh_out_mode()
        self._refresh_out_dir()

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self._start_dir())
        if not folder:
            return
        self.settings["out_dir"] = folder
        self.settings["out_mode"] = "folder"
        self.settings["last_dir"] = folder
        self.settings.save()
        self.out_mode.set_value("folder")
        self.settings_panel.refresh_out_mode()
        self._refresh_out_dir()

    def _refresh_out_dir(self) -> None:
        mode = self.settings["out_mode"]
        folder = bool(mode == "folder")
        self.out_dir_label.setVisible(folder)
        self.browse_btn.setVisible(folder)
        if folder:
            path = str(self.settings["out_dir"] or "")
            self.out_dir_label.setText(path or "尚未选择输出文件夹")
            self.out_dir_label.setToolTip(path)
        self.settings_panel.refresh_out_mode()
        self.upscale_panel.refresh()

    def _on_options_changed(self) -> None:
        self._sync_state()
        self._save_timer.start()

    def _flush_settings(self) -> None:
        """把内存里的偏好写入磁盘（断电/强杀也不会丢）。"""
        self._save_timer.stop()
        self.settings.save()

    def open_output_dir(self) -> None:
        target: Path | None = None
        if self.settings["out_mode"] == "folder" and self.settings["out_dir"]:
            target = Path(str(self.settings["out_dir"]))
        elif self._last_output is not None:
            target = self._last_output
        elif self.model.items:
            target = self.model.items[0].path.parent
        if target is None or not target.exists():
            self.toast.show_message("还没有可打开的输出目录", "warning", 2000)
            return
        os.startfile(str(target))  # noqa: S606 - Windows 专用

    # ------------------------------------------------------------- 模式 --
    def _is_busy(self) -> bool:
        return self._worker is not None or self._upscale_worker is not None

    def _on_mode_changed(self, mode: str) -> None:
        if self._is_busy():
            self.nav.set_value(self.mode)
            self.toast.show_message("请等待当前批处理结束", "warning", 1800)
            return
        self.mode = mode
        self.settings["mode"] = mode
        self._save_timer.start()
        self.panel_stack.setCurrentWidget(self.settings_panel if mode == "convert" else self.upscale_panel)
        self._refresh_mode_texts()
        self._sync_state()
        self._refresh_idle_status()

    def _refresh_mode_texts(self) -> None:
        verb, pending_word, running_word, done_word = MODE_NOUNS.get(self.mode, MODE_NOUNS["convert"])
        self.start_btn.setText(f"开始{verb}")
        self.file_panel.set_hint("双击预览 · Delete 移除")

    def start_current(self) -> None:
        if self.mode == "upscale":
            self.start_upscale()
        else:
            self.start_conversion()

    # ------------------------------------------------------------- 转换 --
    def start_conversion(self) -> None:
        if self._is_busy() or not self.model.items:
            return
        targets = [item for item in self.model.items if item.status in ("idle", "failed", "done")]
        if not targets:
            self.toast.show_message("没有可转换的图片", "warning", 2000)
            return
        options = self.settings_panel.options()
        out_root: Path | None = None
        keep_tree = False
        base_dir: Path | None = None
        if self.settings["out_mode"] == "folder":
            raw = str(self.settings["out_dir"] or "")
            if not raw:
                self.toast.show_message("请先选择输出文件夹", "warning", 2400)
                self.choose_output_dir()
                return
            out_root = Path(raw)
            out_root.mkdir(parents=True, exist_ok=True)
            keep_tree = bool(self.settings["keep_tree"])
            if keep_tree:
                try:
                    base_dir = Path(os.path.commonpath([str(item.path.parent) for item in targets]))
                except ValueError:
                    base_dir = None
        self._convert_started = len(targets)
        self._convert_done = 0
        self._convert_failed = 0
        self._last_output = None
        for item in targets:
            row = self.model.row_of(item.path)
            if row is not None:
                self.model.set_status(row, "running")
        self.progress.set_value(0.0)
        self.progress.set_indeterminate(False)
        self.status_label.setText(f"正在转换 0/{len(targets)}…")
        self.set_controls_enabled(False)
        self.cancel_btn.setEnabled(True)

        worker = ConversionWorker(
            [item.path for item in targets],
            options,
            out_root,
            keep_tree,
            base_dir,
            self,
        )
        worker.progressed.connect(self._on_convert_progress)
        worker.resulted.connect(self._on_convert_result)
        worker.finished_batch.connect(self._on_convert_finished)
        self._worker = worker
        worker.start()

    def cancel_conversion(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")
        if self._upscale_worker is not None:
            self._upscale_worker.cancel()
            self.status_label.setText("正在取消…")

    def _on_convert_progress(self, done: int, total: int, name: str) -> None:
        self.progress.set_value(done / max(1, total))
        self.status_label.setText(f"正在转换 {done}/{total} · {name}")

    def _on_convert_result(self, result: converter.ConvertResult) -> None:
        row = self.model.row_of(result.src)
        if row is None:
            return
        self.model.set_result(row, result)
        if result.ok:
            self._convert_done += 1
            self._last_output = result.dest.parent if result.dest else None
        else:
            self._convert_failed += 1
        self.file_panel.set_counts(*self._counts())

    def _on_convert_finished(self, results, cancelled: bool) -> None:
        self._worker = None
        self.progress.set_indeterminate(False)
        total = len(results)
        failed = sum(1 for r in results if not r.ok)
        done = total - failed
        self.progress.set_value(1.0 if not cancelled else self.progress._value)
        self._after_model_change()
        if cancelled:
            self.status_label.setText(f"已取消 · 完成 {done}/{total}")
            self.toast.show_message("已取消", "warning", 2000)
        elif failed:
            self.status_label.setText(f"完成 {done} · 失败 {failed}")
            self.toast.show_message(f"{failed} 张转换失败，见红色状态", "error", 3000)
        else:
            self.status_label.setText(f"全部完成 · 共 {done} 张")
            self.toast.show_message(f"已完成 {done} 张", "success", 2400)
        self.set_controls_enabled(True)
        self.cancel_btn.setEnabled(False)

    # ------------------------------------------------------------- 放大 --
    def _on_models_changed(self, _ready: bool) -> None:
        self._sync_state()
        self._refresh_idle_status()

    def start_upscale(self) -> None:
        if not self.upscale_panel.selected_ready():
            self.toast.show_message("所选模型的模型包还没下载（右侧卡片）", "warning", 2600)
            return
        if self._is_busy() or not self.model.items:
            return
        targets = [item for item in self.model.items if item.status in ("idle", "failed", "done")]
        if not targets:
            self.toast.show_message("没有可放大的图片", "warning", 2000)
            return
        options = self.upscale_panel.options()
        out_root: Path | None = None
        keep_tree = False
        base_dir: Path | None = None
        if self.settings["out_mode"] == "folder":
            raw = str(self.settings["out_dir"] or "")
            if not raw:
                self.toast.show_message("请先选择输出文件夹", "warning", 2400)
                self.choose_output_dir()
                return
            out_root = Path(raw)
            out_root.mkdir(parents=True, exist_ok=True)
            keep_tree = bool(self.settings["keep_tree"])
            if keep_tree:
                try:
                    base_dir = Path(os.path.commonpath([str(item.path.parent) for item in targets]))
                except ValueError:
                    base_dir = None
        self._convert_started = len(targets)
        self._convert_done = 0
        self._convert_failed = 0
        self._last_output = None
        for item in targets:
            row = self.model.row_of(item.path)
            if row is not None:
                self.model.set_status(row, "running")
        self.progress.set_value(0.0)
        self.progress.set_indeterminate(False)
        self.status_label.setText(f"正在放大 0/{len(targets)}…")
        self.set_controls_enabled(False)
        self.cancel_btn.setEnabled(True)

        worker = UpscaleWorker(
            [item.path for item in targets],
            options,
            out_root,
            keep_tree,
            base_dir,
            self,
        )
        worker.progressed.connect(self._on_upscale_progress)
        worker.resulted.connect(self._on_upscale_result)
        worker.finished_batch.connect(self._on_upscale_finished)
        self._upscale_worker = worker
        worker.start()

    def _on_upscale_progress(self, done: int, total: int, name: str) -> None:
        self.progress.set_value(done / max(1, total))
        self.status_label.setText(f"正在放大 {done}/{total} · {name}")

    def _on_upscale_result(self, result) -> None:
        row = self.model.row_of(result.src)
        if row is None:
            return
        self.model.set_result(row, result)
        if result.ok:
            self._convert_done += 1
            self._last_output = result.dest.parent if result.dest else None
        else:
            self._convert_failed += 1
        self.file_panel.set_counts(*self._counts())

    def _on_upscale_finished(self, results, cancelled: bool) -> None:
        self._upscale_worker = None
        self.progress.set_indeterminate(False)
        total = len(results)
        failed = sum(1 for r in results if not r.ok)
        done = total - failed
        self.progress.set_value(1.0 if not cancelled else self.progress._value)
        self._after_model_change()
        if cancelled:
            self.status_label.setText(f"已取消 · 完成 {done}/{total}")
            self.toast.show_message("已取消", "warning", 2000)
        elif failed:
            self.status_label.setText(f"完成 {done} · 失败 {failed}")
            self.toast.show_message(f"{failed} 张放大失败，见红色状态", "error", 3000)
        else:
            self.status_label.setText(f"全部完成 · 共 {done} 张")
            self.toast.show_message(f"已完成 {done} 张", "success", 2400)
        self.set_controls_enabled(True)
        self.cancel_btn.setEnabled(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.add_btn, self.folder_btn, self.paste_btn, self.clear_btn, self.start_btn):
            widget.setEnabled(enabled)
        self.file_panel.list.setEnabled(enabled)
        self.settings_panel.set_enabled(enabled)
        self.upscale_panel.set_enabled(enabled)
        self.nav.setEnabled(enabled)
        self.cancel_btn.setEnabled(not enabled)
        self._refresh_mode_texts()

    def _sync_state(self) -> None:
        total = len(self.model.items)
        ready = sum(1 for item in self.model.items if item.status in ("idle", "failed", "done"))
        verb = MODE_NOUNS.get(self.mode, MODE_NOUNS["convert"])[0]
        busy = self._is_busy()
        models_ok = self.mode != "upscale" or self.upscale_panel.selected_ready()
        self.start_btn.setEnabled(not busy and ready > 0 and models_ok)
        self.start_btn.setText(f"开始{verb} · {ready}" if ready else f"开始{verb}")
        self.clear_btn.setEnabled(not busy and total > 0)
        self.progress.setVisible(busy or self.progress._value > 0)
        if total:
            self.file_panel.set_counts(*self._counts())
        self._refresh_out_dir()

    # ------------------------------------------------------------- 拖拽 --
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.file_panel.drop_zone.set_active(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.file_panel.drop_zone.set_active(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.file_panel.drop_zone.set_active(False)
        files: list[Path] = []
        folders: list[Path] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                folders.append(path)
            elif path.suffix.lower() in converter.INPUT_EXTS:
                files.append(path)
        if files:
            self.add_paths(files)
        if folders and not self._is_busy():
            self._open_picker(folders[0])
        if not files and not folders:
            self.toast.show_message("没有可用的图片", "warning", 2000)
        event.acceptProposedAction()

    # ------------------------------------------------------------- 窗口 --
    def _load_geometry(self) -> None:
        raw = str(self.settings["window"] or "")
        width, height = 1180, 800
        if "x" in raw:
            try:
                w, h = raw.lower().split("x", 1)
                width, height = int(w), int(h)
            except ValueError:
                pass
        self.resize(max(1080, width), max(740, height))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            frame = self.frameGeometry()
            frame.moveCenter(screen.center())
            self.move(frame.topLeft())

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)
        if self._upscale_worker is not None:
            self._upscale_worker.cancel()
            self._upscale_worker.wait(5000)
        self.settings["window"] = f"{self.width()}x{self.height()}"
        self.settings.save()
        self._probe_pool.clear()
        super().closeEvent(event)
