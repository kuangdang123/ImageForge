"""视频转换页：待处理队列 + 视频设置 + 底部进度条。

这一页是自包含的：视频页有自己的队列模型、探测线程、转换线程与底栏，
主窗只负责把它放进 QStackedWidget 并在切换标签时隐藏自己的图片底栏。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, QThread, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import video
from ..settings import Settings
from . import icons
from .queueview import FileModel, FilePanel, QueueItem
from .theme import Theme
from .video_panel import VideoPanel
from .widgets import MiniProgress, Toast


class ProbeVideoTask(QRunnable):
    """后台探测视频信息 + 抽一帧缩略图。"""

    class Signals(QObject):
        done = Signal(int, str, object, object)
        failed = Signal(int, str, str)

    def __init__(self, row: int, path: Path) -> None:
        super().__init__()
        self.row = row
        self.path = path
        self.signals = ProbeVideoTask.Signals()

    def run(self) -> None:  # noqa: D401
        info = video.probe_video(self.path)
        if not info.ok:
            self.signals.failed.emit(self.row, str(self.path), info.message or "无法读取")
            return
        image = None
        try:
            thumb = video.make_thumbnail(self.path, 80)
            image = self._to_qimage(thumb)
        except Exception:  # 缩略图失败不影响转换
            image = None
        self.signals.done.emit(self.row, str(self.path), info, image)

    @staticmethod
    def _to_qimage(thumb) -> QImage | None:
        if thumb is None:
            return None
        if isinstance(thumb, QImage):
            return thumb
        if isinstance(thumb, (bytes, bytearray)):
            image = QImage()
            image.loadFromData(bytes(thumb))
            return None if image.isNull() else image
        # PIL.Image
        rgb = thumb.convert("RGBA")
        data = rgb.tobytes("raw", "RGBA")
        image = QImage(data, rgb.width, rgb.height, QImage.Format.Format_RGBA8888)
        return image.copy()


class VideoWorker(QThread):
    progressed = Signal(int, int, str)
    fraction = Signal(float)
    resulted = Signal(object)
    finished_batch = Signal(list, bool)

    def __init__(self, paths: list[Path], options: video.VideoOptions, out_root: Path | None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = list(paths)
        self.options = options
        self.out_root = out_root
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        results: list[video.VideoResult] = []
        total = len(self.paths)

        def on_result(index: int, result: video.VideoResult) -> None:
            results.append(result)
            self.resulted.emit(result)
            self.progressed.emit(index + 1, total, result.src.name)

        video.run_batch(
            self.paths,
            self.options,
            self.out_root,
            on_result=on_result,
            should_cancel=lambda: self._cancelled,
            on_progress=self.fraction.emit,
        )
        self.finished_batch.emit(results, self._cancelled)


class VideoPage(QWidget):
    toast_requested = Signal(str, str)

    def __init__(self, theme: Theme, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.settings = settings
        self._pending_probe = 0
        self._worker: VideoWorker | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._tasks: list[ProbeVideoTask] = []
        self._auto_start = False
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        outer.addLayout(self._build_toolbar())
        outer.addWidget(self._build_ffmpeg_banner())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.model = FileModel(theme, self)
        self.file_panel = FilePanel(theme)
        self.file_panel.set_hint("双击播放 · Delete 移除")
        self.file_panel.add_clicked.connect(self.add_files)
        self.file_panel.remove_requested.connect(self._remove_row)
        self.file_panel.preview_requested.connect(self._open_in_player)
        self.file_panel.list.setModel(self.model)
        self.file_panel.list.files_dropped.connect(lambda paths: self.add_paths(paths))
        body.addWidget(self.file_panel, 1)

        right = QWidget()
        right.setFixedWidth(356)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.panel = VideoPanel(theme, settings)
        self.panel.changed.connect(self._on_options_changed)
        self.panel.pick_dir_clicked.connect(self.choose_output_dir)
        right_layout.addWidget(self.panel)
        body.addWidget(right)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_footer())
        self.toast = Toast(self, theme)

        self._apply_ffmpeg_state()
        self._refresh_idle_status()
        self._sync_state()

    # ------------------------------------------------------------- 构建 --
    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.add_btn = QPushButton(" 添加视频")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.setIcon(icons.icon("plus", "#FFFFFF", 16))
        self.add_btn.setIconSize(QSize(16, 16))
        self.add_btn.clicked.connect(self.add_files)

        self.folder_btn = QPushButton(" 添加文件夹")
        self.folder_btn.setProperty("variant", "secondary")
        self.folder_btn.setIcon(icons.icon("folder", self.theme.color("text_primary"), 16))
        self.folder_btn.setIconSize(QSize(16, 16))
        self.folder_btn.clicked.connect(self.add_folder)

        self.clear_btn = QPushButton(" 清空")
        self.clear_btn.setProperty("variant", "ghost")
        self.clear_btn.setIcon(icons.icon("trash", self.theme.color("text_secondary"), 16))
        self.clear_btn.setIconSize(QSize(16, 16))
        self.clear_btn.clicked.connect(self.clear_all)

        row.addWidget(self.add_btn)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        row.addWidget(self.clear_btn)
        return row

    def _build_ffmpeg_banner(self) -> QWidget:
        self.banner = QWidget()
        self.banner.setObjectName("Banner")
        layout = QVBoxLayout(self.banner)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        self.banner_title = QLabel("没有找到 ffmpeg")
        self.banner_title.setFont(self.theme.font("body_bold"))
        self.banner_body = QLabel("")
        self.banner_body.setObjectName("Caption")
        self.banner_body.setWordWrap(True)
        self.banner_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.banner_title)
        layout.addWidget(self.banner_body)
        self.retry_btn = QPushButton("重新检测")
        self.retry_btn.setProperty("variant", "secondary")
        self.retry_btn.setFixedHeight(28)
        self.retry_btn.clicked.connect(self._retry_ffmpeg)
        layout.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.banner.setVisible(False)
        return self.banner

    def _retry_ffmpeg(self) -> None:
        video.reset_tool_cache()
        self._apply_ffmpeg_state()
        if video.ffmpeg_available():
            self._toast("已找到 ffmpeg，可以开始转换了", "success")
        else:
            self._toast("还是没有找到 ffmpeg，检查一下安装路径", "warning")

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(8)
        self.progress = MiniProgress(self.theme)
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Caption")
        row.addWidget(self.status_label)
        row.addStretch(1)

        self.open_dir_btn = QPushButton(" 打开输出目录")
        self.open_dir_btn.setProperty("variant", "ghost")
        self.open_dir_btn.setIcon(icons.icon("external", self.theme.color("text_secondary"), 16))
        self.open_dir_btn.setIconSize(QSize(16, 16))
        self.open_dir_btn.clicked.connect(self.open_output_dir)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setProperty("variant", "secondary")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setEnabled(False)

        self.start_btn = QPushButton("开始转换")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setMinimumWidth(132)
        self.start_btn.clicked.connect(self.start)

        row.addWidget(self.open_dir_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.start_btn)
        layout.addLayout(row)
        return footer

    # ------------------------------------------------------------- 添加 --
    def add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(video.VIDEO_INPUT_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", self.settings["last_dir"] or "", f"视频文件 ({patterns});;所有文件 (*.*)"
        )
        if paths:
            self.settings["last_dir"] = str(Path(paths[0]).parent)
            self.add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹", self.settings["last_dir"] or "")
        if not folder:
            return
        self.settings["last_dir"] = folder
        found = video.collect_videos(Path(folder), recursive=bool(self.settings["recursive"]))
        if not found:
            self._toast("这个文件夹里没有视频文件", "warning")
            return
        self.add_paths(found)

    def add_paths(self, paths: list[Path], *, autostart: bool = False) -> None:
        if not paths:
            return
        candidates = [Path(p) for p in paths if Path(p).suffix.lower() in video.VIDEO_INPUT_EXTS]
        if not candidates:
            self._toast("只支持视频文件（mp4 / mkv / mov / webm / avi / gif 等）", "warning")
            return
        rows = self.model.add_paths(candidates)
        if not rows:
            self._toast("这些视频已经在列表里了", "info")
            return
        for row in rows:
            item = self.model.item(row)
            if item is not None:
                item.kind = "video"
                item.meta_override = "正在读取…"
            self._schedule_probe(row)
        self.file_panel.show_list()
        self.file_panel.set_counts(*self._counts())
        if autostart:
            self._auto_start = True
        self._sync_state()
        self._refresh_idle_status()

    def _schedule_probe(self, row: int) -> None:
        item = self.model.item(row)
        if item is None:
            return
        self._pending_probe += 1
        task = ProbeVideoTask(row, item.path)
        task.signals.done.connect(self._on_probe_done)
        task.signals.failed.connect(self._on_probe_failed)
        self._tasks.append(task)
        self._pool.start(task)

    def _on_probe_done(self, row: int, _path: str, info, image) -> None:
        self._pending_probe = max(0, self._pending_probe - 1)
        item = self.model.item(row)
        if item is not None:
            seconds = info.duration or 0
            minutes = int(seconds // 60)
            rest = int(round(seconds % 60))
            parts = [f"{info.width}×{info.height}", f"{minutes}:{rest:02d}"]
            if info.fps:
                parts.append(f"{info.fps:.0f} fps")
            parts.append(video.human_size(info.bytes))
            parts.append((info.video_codec or info.format or "").upper())
            item.info = None
            item.meta_override = " · ".join(part for part in parts if part)
            if image is not None and not image.isNull():
                item.thumb = QPixmap.fromImage(image)
            self.model.set_status(row, "idle")
        self.file_panel.set_counts(*self._counts())
        self._after_probe()

    def _on_probe_failed(self, row: int, _path: str, message: str) -> None:
        self._pending_probe = max(0, self._pending_probe - 1)
        self.model.set_status(row, "failed", message=message)
        self.file_panel.set_counts(*self._counts())
        self._after_probe()

    def _after_probe(self) -> None:
        self._sync_state()
        if self._pending_probe == 0:
            self._refresh_idle_status()
            if self._auto_start:
                self._auto_start = False
                self.start()

    def _remove_row(self, row: int) -> None:
        if self._is_busy():
            self._toast("转换中不能移除，先取消", "warning")
            return
        self.model.remove_rows([row])
        if not self.model.items:
            self.file_panel.show_empty()
        self.file_panel.set_counts(*self._counts())
        self._sync_state()
        self._refresh_idle_status()

    def clear_all(self) -> None:
        if self._is_busy():
            self._toast("转换中不能清空，先取消", "warning")
            return
        self.model.clear()
        self.file_panel.show_empty()
        self.file_panel.set_counts(0, 0, 0)
        self._sync_state()
        self._refresh_idle_status()

    def _open_in_player(self, row: int) -> None:
        item = self.model.item(row)
        if item is None:
            return
        try:
            os.startfile(str(item.path))  # noqa: S606
        except OSError:
            self._toast("打不开这个文件", "error")

    # ------------------------------------------------------------- 转换 --
    def start(self) -> None:
        if self._is_busy() or not video.ffmpeg_available():
            if not video.ffmpeg_available():
                self._toast("没有找到 ffmpeg，先按提示安装", "error")
            return
        targets = [item for item in self.model.items if item.status in ("idle", "failed", "done")]
        if not targets:
            self._toast("没有可转换的视频", "warning")
            return
        options = self.panel.options()
        out_root = self._output_root(targets)
        for item in targets:
            item.result = None
            item.message = ""
            row = self.model.row_of(item.path)
            if row is not None:
                self.model.set_status(row, "idle")
        self._worker = VideoWorker([item.path for item in targets], options, out_root, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.fraction.connect(self._on_fraction)
        self._worker.resulted.connect(self._on_result)
        self._worker.finished_batch.connect(self._on_finished)
        self.progress.set_indeterminate(False)
        self.progress.set_value(0.0)
        self.status_label.setText(f"转换中 0/{len(targets)} · 0%")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._set_controls_enabled(False)
        self._worker.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")
            self.cancel_btn.setEnabled(False)

    def _output_root(self, targets: list[QueueItem]) -> Path | None:
        if self.settings["out_mode"] != "folder":
            return None
        folder = str(self.settings["out_dir"])
        if not folder:
            return None
        root = Path(folder)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._toast("输出目录不可写，改为保存在源文件旁", "warning")
            return None
        return root

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self._base_done = done - 1
        self._base_total = max(1, total)
        self.progress.set_indeterminate(False)
        self.progress.set_value((done - 1) / max(1, total))
        percent = int(round((done - 1) / max(1, total) * 100))
        self.status_label.setText(f"转换中 {done}/{total} · {percent}% · {name}")

    def _on_fraction(self, value: float) -> None:
        total = getattr(self, "_base_total", 0) or 1
        done = getattr(self, "_base_done", 0)
        self.progress.set_indeterminate(False)
        fraction = (done + max(0.0, min(1.0, value))) / total
        self.progress.set_value(fraction)
        percent = int(round(fraction * 100))
        current = self.status_label.text()
        head = current.split(" · ")[:2]
        if len(head) == 2:
            self.status_label.setText(" · ".join([head[0], f"{percent}%"] + current.split(" · ")[2:]))

    def _on_result(self, result: video.VideoResult) -> None:
        path = Path(result.src)
        row = self.model.row_of(path)
        if row is not None:
            self.model.set_result(row, result)
        self.file_panel.set_counts(*self._counts())
        if result.warnings:
            self._collect_warning(result.warnings[0])

    def _collect_warning(self, text: str) -> None:
        warnings = getattr(self, "_warnings", None)
        if warnings is None:
            warnings = self._warnings = []
        if text not in warnings:
            warnings.append(text)

    def _on_finished(self, results: list, cancelled: bool) -> None:
        self.progress.set_indeterminate(False)
        if not cancelled:
            self.progress.set_value(1.0)
        done = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok)
        self.status_label.setText(f"{'已取消' if cancelled else '全部完成'} · 成功 {done}" + (f" · 失败 {failed}" if failed else ""))
        self._worker = None
        self.cancel_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        self._set_controls_enabled(True)
        self._sync_state()
        warnings = getattr(self, "_warnings", [])
        self._warnings = []
        if done:
            message = f"{'已取消' if cancelled else '已转换'} {done} 个视频"
            if warnings:
                message += f"（{warnings[0]}）"
            self._toast(message, "success" if not cancelled else "info")
        if results:
            last = results[-1]
            if last.dest:
                self.settings["last_dir"] = str(Path(last.dest).parent)
        self._refresh_idle_status()

    # ------------------------------------------------------------- 状态 --
    def is_running(self) -> bool:
        return self._worker is not None

    def _is_busy(self) -> bool:
        return self._worker is not None

    def _counts(self) -> tuple[int, int, int]:
        counts = self.model.status_counts()
        total = len(self.model.items)
        return total, counts.get("done", 0), counts.get("failed", 0)

    def _sync_state(self) -> None:
        targets = [item for item in self.model.items if item.status in ("idle", "failed", "done")]
        self.start_btn.setText(f"开始转换 · {len(targets)}" if targets else "开始转换")
        self.start_btn.setEnabled(bool(targets) and not self._is_busy() and video.ffmpeg_available())
        self.clear_btn.setEnabled(bool(self.model.items) and not self._is_busy())

    def _refresh_idle_status(self) -> None:
        if self._is_busy():
            return
        total = len(self.model.items)
        counts = self.model.status_counts()
        if total == 0:
            self.status_label.setText("拖入视频文件，或按 Ctrl+O 添加")
        elif self._pending_probe:
            self.status_label.setText(f"正在读取信息… {self._pending_probe} 个")
        else:
            pending = counts.get("idle", 0) + counts.get("failed", 0)
            done = counts.get("done", 0)
            if pending:
                self.status_label.setText(f"共 {total} 个视频 · {pending} 个待转换")
            else:
                self.status_label.setText(f"共 {total} 个视频 · 已完成 {done}")

    def _on_options_changed(self) -> None:
        self._sync_state()
        self.settings.save()

    def _apply_ffmpeg_state(self) -> None:
        available = video.ffmpeg_available()
        self.banner.setVisible(not available)
        if not available:
            self.banner_body.setText(video.ffmpeg_hint())
        self.start_btn.setToolTip("" if available else "需要先安装 ffmpeg")
        self._sync_state()

    def set_controls_enabled(self, enabled: bool) -> None:
        self._set_controls_enabled(enabled)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.add_btn.setEnabled(enabled)
        self.folder_btn.setEnabled(enabled)
        self.panel.set_enabled(enabled)
        if not enabled:
            self.start_btn.setEnabled(False)

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.settings["out_dir"] or "")
        if not folder:
            return
        self.settings["out_dir"] = folder
        self.settings["out_mode"] = "folder"
        self.panel.refresh()
        self.settings.save()

    def open_output_dir(self) -> None:
        if self.settings["out_mode"] == "folder" and self.settings["out_dir"]:
            target = Path(str(self.settings["out_dir"]))
        else:
            done = [item for item in self.model.items if item.status == "done"]
            if done and done[-1].result and getattr(done[-1].result, "dest", None):
                target = Path(done[-1].result.dest).parent
            else:
                target = Path(self.settings["last_dir"] or Path.home())
        if not target.exists():
            self._toast("目录还不存在", "warning")
            return
        os.startfile(str(target))  # noqa: S606

    def _toast(self, text: str, kind: str = "info") -> None:
        self.toast.show_message(text, kind)
        self.toast_requested.emit(text, kind)

    # ------------------------------------------------------------- 拖拽 --
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_hover(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drag_hover(False)

    def _drag_hover(self, on: bool) -> None:
        if hasattr(self.file_panel, "drop_zone"):
            self.file_panel.drop_zone.set_active(on)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._drag_hover(False)
        urls = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        files: list[Path] = []
        for path in urls:
            if path.is_dir():
                files.extend(video.collect_videos(path, recursive=bool(self.settings["recursive"])))
            elif path.suffix.lower() in video.VIDEO_INPUT_EXTS:
                files.append(path)
        if files:
            self.add_paths(files)
        else:
            self._toast("没有可用的视频文件", "warning")

    def refresh_icons(self) -> None:
        theme = self.theme
        self.add_btn.setIcon(icons.icon("plus", "#FFFFFF", 16))
        self.folder_btn.setIcon(icons.icon("folder", theme.color("text_primary"), 16))
        self.clear_btn.setIcon(icons.icon("trash", theme.color("text_secondary"), 16))
        self.open_dir_btn.setIcon(icons.icon("external", theme.color("text_secondary"), 16))
        self.file_panel.list.viewport().update()

    # ------------------------------------------------------------- 收尾 --
    def shortcut(self, key: str) -> bool:
        if key == "open":
            self.add_files()
            return True
        if key == "folder":
            self.add_folder()
            return True
        if key == "start":
            self.start()
            return True
        if key == "cancel":
            self.cancel()
            return True
        return False

    def close_cleanup(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)
            self._worker = None
        self._pool.waitForDone(1500)


__all__ = ["VideoPage", "VideoWorker"]
