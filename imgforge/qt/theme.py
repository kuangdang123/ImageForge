"""主题：语义 token → QSS / QColor / QFont，明暗两套即时切换。

UI 代码里禁止出现字面量色值，一律走 `theme.color(role)` / `theme.qcolor(role)`，
这样"换品牌色 / 加第三套主题"只动 `tokens.py`。
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from . import icons
from . import tokens

MODES = ("system", "light", "dark")


class Theme(QObject):
    changed = Signal()

    def __init__(self, mode: str = "system") -> None:
        super().__init__()
        self._mode = mode if mode in MODES else "system"
        self._icon_cache: Dict[tuple[str, str, int], QIcon] = {}
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme)

    # ------------------------------------------------------------ 模式 --
    @property
    def mode(self) -> str:
        return self._mode

    def effective_mode(self) -> str:
        if self._mode == "system":
            hints = QGuiApplication.styleHints()
            scheme = getattr(hints, "colorScheme", lambda: None)()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            return "light"
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = mode if mode in MODES else "system"
        if mode == self._mode:
            return
        self._mode = mode
        self._icon_cache.clear()
        self.apply()
        self.changed.emit()

    def _on_system_scheme(self, *_args) -> None:
        if self._mode == "system":
            self._icon_cache.clear()
            self.apply()
            self.changed.emit()

    def is_dark(self) -> bool:
        return self.effective_mode() == "dark"

    # ------------------------------------------------------------ token --
    def color(self, role: str, mode: str | None = None) -> str:
        palette = tokens.PALETTES[mode or self.effective_mode()]
        try:
            return palette[role]
        except KeyError as exc:  # pragma: no cover - 拼错角色名应当立刻暴露
            raise KeyError(f"未知的颜色角色: {role}") from exc

    def qcolor(self, role: str, alpha: int | None = None) -> QColor:
        color = QColor(self.color(role))
        if alpha is not None:
            color.setAlpha(alpha)
        return color

    def space(self, name: str) -> int:
        return tokens.SPACE[name]

    def px(self, value: float) -> int:
        """Qt 自己处理 DPI，这里的“逻辑像素”就是设备无关的整数像素。

        保留这个方法是为了让自绘控件里的尺寸表达式读起来和旧 Tk 版一致。
        """
        return int(round(value))

    def radius(self, name: str) -> int:
        return tokens.RADIUS[name]

    def duration(self, name: str) -> int:
        return tokens.DURATIONS[name]

    def font(self, name: str = "body") -> QFont:
        size, weight = tokens.FONTS[name]
        font = QFont(tokens.FONT_FAMILY)
        font.setPixelSize(size)
        font.setWeight(QFont.Weight(weight))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def mono_font(self, size: int = 12) -> QFont:
        font = QFont(tokens.MONO_FAMILY)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(size)
        return font

    def icon(self, name: str, role: str = "text_secondary", size: int = 20) -> QIcon:
        color = self.color(role)
        key = (name, color, size)
        cached = self._icon_cache.get(key)
        if cached is None:
            cached = icons.icon(name, color, size)
            self._icon_cache[key] = cached
        return cached

    # -------------------------------------------------------------- 应用 --
    def apply(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            app.setPalette(self._qpalette())
            app.setStyleSheet(self.qss())

    def _qpalette(self) -> QPalette:
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, self.qcolor("window"))
        pal.setColor(QPalette.ColorRole.WindowText, self.qcolor("text_primary"))
        pal.setColor(QPalette.ColorRole.Base, self.qcolor("surface"))
        pal.setColor(QPalette.ColorRole.AlternateBase, self.qcolor("surface_sunken"))
        pal.setColor(QPalette.ColorRole.Text, self.qcolor("text_primary"))
        pal.setColor(QPalette.ColorRole.Button, self.qcolor("surface"))
        pal.setColor(QPalette.ColorRole.ButtonText, self.qcolor("text_primary"))
        pal.setColor(QPalette.ColorRole.Highlight, self.qcolor("accent"))
        pal.setColor(QPalette.ColorRole.HighlightedText, self.qcolor("on_accent"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, self.qcolor("surface_raised"))
        pal.setColor(QPalette.ColorRole.ToolTipText, self.qcolor("text_primary"))
        pal.setColor(QPalette.ColorRole.PlaceholderText, self.qcolor("text_disabled"))
        return pal

    # ---------------------------------------------------------------- QSS --
    def qss(self) -> str:
        c = {role: self.color(role) for role in tokens.LIGHT}
        family = tokens.FONT_FAMILY
        body = tokens.FONTS["body"][0]
        caption = tokens.FONTS["caption"][0]
        radius_sm = tokens.RADIUS["sm"]
        radius_md = tokens.RADIUS["md"]
        radius_lg = tokens.RADIUS["lg"]
        return f"""
        * {{
            font-family: "{family}";
            font-size: {body}px;
            outline: none;
        }}
        QWidget {{
            color: {c['text_primary']};
        }}
        QWidget#Root, QMainWindow, QDialog {{
            background: {c['window']};
        }}
        QWidget#Header {{
            background: {c['window']};
        }}
        QFrame#Card {{
            background: {c['surface']};
            border: 1px solid {c['border']};
            border-radius: {radius_lg}px;
        }}
        QFrame#Card QFrame#Card {{
            background: {c['surface']};
        }}
        QLabel#Display {{
            font-size: {tokens.FONTS['display'][0]}px;
            font-weight: 600;
        }}
        QLabel#H1 {{
            font-size: {tokens.FONTS['h1'][0]}px;
            font-weight: 600;
        }}
        QLabel#H2 {{
            font-size: {tokens.FONTS['h2'][0]}px;
            font-weight: 600;
        }}
        QLabel#Caption {{
            font-size: {caption}px;
            color: {c['text_secondary']};
        }}
        QLabel#Muted {{
            color: {c['text_secondary']};
        }}
        QLabel#Disabled {{
            color: {c['text_disabled']};
        }}
        QLabel#VersionBadge {{
            background: {c['accent_subtle']};
            color: {c['accent_text']};
            border-radius: 7px;
            padding: 2px 7px;
            font-size: {caption}px;
            font-weight: 600;
        }}
        QLabel#AccentText {{
            color: {c['accent_text']};
            font-weight: 500;
        }}
        QLabel#Error {{
            color: {c['danger']};
        }}
        QLabel#Success {{
            color: {c['success']};
        }}
        QWidget#Banner {{
            background: {c['warning_subtle']};
            border: 1px solid {c['warning']};
            border-radius: {radius_md}px;
        }}
        QWidget#Banner QLabel {{
            color: {c['text_primary']};
            background: transparent;
        }}

        /* ------------------------------------------------------- 按钮 -- */
        QPushButton {{
            background: transparent;
            color: {c['text_primary']};
            border: 1px solid transparent;
            border-radius: {radius_md}px;
            padding: 0 14px;
            min-height: 34px;
            font-size: {body}px;
        }}
        QPushButton[variant="secondary"] {{
            background: {c['surface']};
            border: 1px solid {c['border']};
        }}
        QPushButton[variant="secondary"]:hover {{
            background: {c['surface_hover']};
            border-color: {c['border_strong']};
        }}
        QPushButton[variant="secondary"]:pressed {{
            background: {c['surface_sunken']};
        }}
        QPushButton[variant="ghost"] {{
            color: {c['text_secondary']};
            padding: 0 10px;
        }}
        QPushButton[variant="ghost"]:hover {{
            background: {c['surface_hover']};
            color: {c['text_primary']};
        }}
        QPushButton[variant="ghost"]:pressed {{
            background: {c['surface_sunken']};
        }}
        QPushButton[variant="primary"] {{
            background: {c['accent']};
            color: {c['on_accent']};
            font-weight: 600;
            padding: 0 20px;
            min-height: 38px;
        }}
        QPushButton[variant="primary"]:hover {{
            background: {c['accent_hover']};
        }}
        QPushButton[variant="primary"]:pressed {{
            background: {c['accent_active']};
        }}
        QPushButton[variant="danger"] {{
            background: {c['danger']};
            color: #FFFFFF;
            font-weight: 500;
            min-height: 38px;
        }}
        QPushButton[variant="dangerGhost"] {{
            color: {c['danger']};
        }}
        QPushButton[variant="dangerGhost"]:hover {{
            background: {c['danger_subtle']};
        }}
        QPushButton:disabled {{
            color: {c['text_disabled']};
            background: {c['surface_sunken']};
            border-color: transparent;
        }}
        QPushButton[variant="primary"]:disabled {{
            background: {c['surface_sunken']};
            color: {c['text_disabled']};
        }}
        QPushButton#IconButton {{
            min-width: 32px;
            max-width: 32px;
            min-height: 32px;
            max-height: 32px;
            padding: 0;
            border-radius: {radius_sm}px;
            background: transparent;
        }}
        QPushButton#IconButton:hover {{
            background: {c['surface_hover']};
        }}
        QPushButton#IconButton:pressed {{
            background: {c['surface_sunken']};
        }}
        QPushButton#IconButton:disabled {{
            background: transparent;
            color: {c['text_disabled']};
        }}

        /* --------------------------------------------------- 数值输入 -- */
        QSpinBox, QDoubleSpinBox {{
            background: {c['surface']};
            border: 1px solid {c['border']};
            border-radius: {radius_sm}px;
            padding: 4px 8px;
            min-height: 22px;
            color: {c['text_primary']};
            selection-background-color: {c['accent']};
            selection-color: {c['on_accent']};
        }}
        QSpinBox:hover, QDoubleSpinBox:hover {{
            border-color: {c['border_strong']};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {c['focus']};
        }}
        QSpinBox:disabled, QDoubleSpinBox:disabled {{
            color: {c['text_disabled']};
            background: {c['surface_sunken']};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 18px;
            border: none;
            background: transparent;
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image: none;
            width: 0;
            height: 0;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-bottom: 5px solid {c['text_secondary']};
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image: none;
            width: 0;
            height: 0;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {c['text_secondary']};
        }}

        /* ----------------------------------------------------- 输入框 -- */
        QLineEdit {{
            background: {c['surface']};
            border: 1px solid {c['border']};
            border-radius: {radius_sm}px;
            padding: 6px 10px;
            selection-background-color: {c['accent']};
            selection-color: {c['on_accent']};
            min-height: 22px;
        }}
        QLineEdit:hover {{
            border-color: {c['border_strong']};
        }}
        QLineEdit:focus {{
            border: 1px solid {c['focus']};
        }}

        /* --------------------------------------------------- 下拉选择 -- */
        QComboBox#SelectBox {{
            background: {c['surface_raised']};
            border: 1px solid {c['border']};
            border-radius: {radius_md}px;
            padding: 6px 34px 6px 12px;
            min-height: 22px;
            color: {c['text_primary']};
        }}
        QComboBox#SelectBox:hover {{
            border-color: {c['border_strong']};
            background: {c['surface_hover']};
        }}
        QComboBox#SelectBox:focus {{
            border: 1px solid {c['focus']};
        }}
        QComboBox#SelectBox:disabled {{
            color: {c['text_disabled']};
            background: {c['surface']};
        }}
        QComboBox#SelectBox::drop-down {{
            border: none;
            width: 30px;
        }}
        QComboBox#SelectBox::down-arrow {{
            image: none;
            width: 0;
            height: 0;
        }}
        QComboBox#SelectBox QAbstractItemView {{
            background: {c['surface_raised']};
            border: 1px solid {c['border']};
            border-radius: {radius_md}px;
            padding: 4px;
            outline: none;
            selection-background-color: {c['accent_subtle']};
            selection-color: {c['text_primary']};
        }}
        QComboBox#SelectBox QAbstractItemView::item {{
            min-height: 26px;
            padding: 2px 8px;
            border-radius: {radius_sm}px;
        }}

        /* ------------------------------------------------------- 滚动 -- */
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px 2px 2px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border_strong']};
            border-radius: 3px;
            min-height: 32px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c['text_disabled']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
        }}
        QScrollBar::handle:horizontal {{
            background: {c['border_strong']};
            border-radius: 3px;
            min-width: 32px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}

        /* ------------------------------------------------------- 列表 -- */
        QListView#FileList {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QListWidget::item {{
            border-radius: {radius_md}px;
        }}
        QToolTip {{
            background: {c['surface_raised']};
            color: {c['text_primary']};
            border: 1px solid {c['border']};
            border-radius: {radius_sm}px;
            padding: 6px 8px;
        }}

        /* ------------------------------------------------------- 滑块 -- */
        QSlider::groove:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {c['surface_sunken']};
        }}
        QSlider::sub-page:horizontal {{
            height: 4px;
            border-radius: 2px;
            background: {c['accent']};
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -6px 0;
            border-radius: 8px;
            background: {c['surface']};
            border: 2px solid {c['accent']};
        }}
        QSlider::handle:horizontal:hover {{
            border-color: {c['accent_hover']};
        }}

        /* --------------------------------------------------- 菜单/对话框 -- */
        QMenu {{
            background: {c['surface_raised']};
            border: 1px solid {c['border']};
            border-radius: {radius_md}px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 20px 7px 14px;
            border-radius: {radius_sm}px;
        }}
        QMenu::item:selected {{
            background: {c['accent_subtle']};
            color: {c['accent_text']};
        }}
        QMenu::separator {{
            height: 1px;
            background: {c['border']};
            margin: 6px 8px;
        }}
        QDialog {{
            background: {c['window']};
        }}
        QMessageBox {{
            background: {c['window']};
        }}
        QProgressBar {{
            background: {c['surface_sunken']};
            border: none;
            border-radius: 2px;
            height: 4px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            border-radius: 2px;
            background: {c['accent']};
        }}
        """
