"""矢量图标：全部用 QPainter 现画，随主题色与 DPI 自适应。

编辑器 / 设计稿里没有引入图标字体，避免"某个字形在别的机器上缺字"。
坐标统一按 24×24 的画布设计，绘制时等比缩放到目标矩形。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _stroke(painter: QPainter, path: QPainterPath, color: QColor, width: float) -> None:
    painter.setPen(_pen(color, width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _path(points: list[tuple[float, float]], close: bool = False) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    if close:
        path.closeSubpath()
    return path


# ---------------------------------------------------------------- 各图标 --
def _plus(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(12, 5.5), (12, 18.5)]), c, w)
    _stroke(p, _path([(5.5, 12), (18.5, 12)]), c, w)


def _folder(p: QPainter, c: QColor, w: float) -> None:
    path = QPainterPath(QPointF(4.0, 7.5))
    path.lineTo(9.2, 7.5)
    path.lineTo(11.0, 9.8)
    path.lineTo(20.0, 9.8)
    path.lineTo(20.0, 17.6)
    path.lineTo(4.0, 17.6)
    path.closeSubpath()
    _stroke(p, path, c, w)


def _clipboard(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5.2, 5.2, 13.6, 14.6), 2.6, 2.6)
    p.drawRoundedRect(QRectF(9.0, 3.4, 6.0, 3.6), 1.4, 1.4)


def _trash(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(4.5, 7.0), (19.5, 7.0)]), c, w)
    _stroke(p, _path([(9.4, 7.0), (9.4, 4.8), (14.6, 4.8), (14.6, 7.0)]), c, w)
    _stroke(p, _path([(6.6, 7.0), (7.4, 19.2), (16.6, 19.2), (17.4, 7.0)]), c, w)
    _stroke(p, _path([(10.2, 10.2), (10.5, 16.2)]), c, w * 0.8)
    _stroke(p, _path([(13.8, 10.2), (13.5, 16.2)]), c, w * 0.8)


def _sun(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 12), 4.2, 4.2)
    for i in range(8):
        angle = math.radians(i * 45)
        r0, r1 = 7.4, 9.6
        _stroke(
            p,
            _path([
                (12 + math.cos(angle) * r0, 12 + math.sin(angle) * r0),
                (12 + math.cos(angle) * r1, 12 + math.sin(angle) * r1),
            ]),
            c,
            w,
        )


def _moon(p: QPainter, c: QColor, w: float) -> None:
    outer = QPainterPath()
    outer.addEllipse(QPointF(12.4, 11.8), 6.1, 6.1)
    cut = QPainterPath()
    cut.addEllipse(QPointF(16.2, 9.2), 5.4, 5.4)
    crescent = outer.subtracted(cut)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(crescent)


def _monitor(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.2, 5.4, 15.6, 10.6), 2.0, 2.0)
    _stroke(p, _path([(12, 16.0), (12, 18.6)]), c, w)
    _stroke(p, _path([(8.6, 19.2), (15.4, 19.2)]), c, w)


def _image(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.0, 5.0, 16.0, 14.0), 2.6, 2.6)
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(9.0, 10.0), 1.5, 1.5)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _stroke(
        p,
        _path([(5.4, 17.2), (10.2, 12.6), (13.0, 15.2), (16.2, 12.2), (18.8, 14.8)]),
        c,
        w,
    )


def _check(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(5.6, 12.6), (10.0, 17.0), (18.4, 7.4)]), c, w * 1.1)


def _chevron_down(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(6.8, 9.6), (12, 15.0), (17.2, 9.6)]), c, w)


def _close(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(6.8, 6.8), (17.2, 17.2)]), c, w)
    _stroke(p, _path([(17.2, 6.8), (6.8, 17.2)]), c, w)


def _external(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(13.4, 5.4), (18.6, 5.4), (18.6, 10.6)]), c, w)
    _stroke(p, _path([(18.4, 5.6), (11.6, 12.4)]), c, w)
    _stroke(
        p,
        _path([(17.0, 14.2), (17.0, 18.0), (6.0, 18.0), (6.0, 7.0), (9.8, 7.0)]),
        c,
        w,
    )


def _copy(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8.4, 8.4, 10.2, 10.2), 2.2, 2.2)
    _stroke(
        p,
        _path([(15.6, 8.4), (15.6, 6.6), (5.4, 6.6), (5.4, 16.8), (7.2, 16.8)]),
        c,
        w,
    )


def _refresh(p: QPainter, c: QColor, w: float) -> None:
    p.setPen(_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5.2, 5.2, 13.6, 13.6), 60 * 16, 250 * 16)
    _stroke(p, _path([(15.4, 3.9), (18.1, 7.0), (14.4, 8.2)]), c, w)


def _sort(p: QPainter, c: QColor, w: float) -> None:
    _stroke(p, _path([(7.6, 6.0), (7.6, 18.0)]), c, w)
    _stroke(p, _path([(4.4, 14.8), (7.6, 18.0), (10.8, 14.8)]), c, w)
    _stroke(p, _path([(16.4, 18.0), (16.4, 6.0)]), c, w)
    _stroke(p, _path([(13.2, 9.2), (16.4, 6.0), (19.6, 9.2)]), c, w)


_DRAWERS = {
    "plus": _plus,
    "folder": _folder,
    "clipboard": _clipboard,
    "trash": _trash,
    "sun": _sun,
    "moon": _moon,
    "monitor": _monitor,
    "image": _image,
    "check": _check,
    "chevron_down": _chevron_down,
    "close": _close,
    "external": _external,
    "copy": _copy,
    "refresh": _refresh,
    "sort": _sort,
}

NAMES = tuple(_DRAWERS)


def draw(painter: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.7) -> None:
    """在 painter 的 rect 内画一枚图标，线宽按 24 单位画布换算。"""
    drawer = _DRAWERS[name]
    scale = min(rect.width(), rect.height()) / 24.0
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(rect.topLeft())
    painter.scale(scale, scale)
    drawer(painter, color, width)
    painter.restore()


def pixmap(name: str, color: str, size: int = 20, ratio: float = 1.0) -> QPixmap:
    physical = max(1, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter, name, QRectF(0, 0, physical, physical), QColor(color))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    result = QIcon()
    for ratio in (1.0, 1.5, 2.0, 3.0):
        result.addPixmap(pixmap(name, color, size, ratio))
    return result
