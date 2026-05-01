#    QPane - High-performance PySide6 image viewer
#    Copyright (C) 2025  Artificial Sweetener and contributors
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Cursor provider that shows pixel coordinates under the hotspot."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)


# Provided by the demo host at execution time:
# - CUSTOM_MODE: str (the tool mode this cursor belongs to)
CUSTOM_MODE = globals().get("CUSTOM_MODE")


def cursor(qpane):
    """Return a crosshair cursor registered via QPane.registerCursorProvider.

    QPane invokes this hook when it needs a cursor for the active tool; return
    None to defer to the default cursor when the tool is inactive.
    """
    if qpane.getControlMode() != CUSTOM_MODE:
        return None
    cache = globals().get("_CURSOR_CACHE")
    if cache is None:
        cache = {}
        globals()["_CURSOR_CACHE"] = cache
    dpr = float(qpane.devicePixelRatioF()) or 1.0
    coords = None
    image = qpane.currentImage
    if image is not None and not image.isNull():
        hit = qpane.panelHitTest(qpane.mapFromGlobal(QCursor.pos()))
        if hit is not None and hit.inside_image:
            coords = (hit.clamped_point.x(), hit.clamped_point.y())
    cache_key = (coords, dpr)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    text = "" if coords is None else f"{coords[0]}, {coords[1]}"
    crosshair_size = 36.0
    label_gap = 8.0
    padding = 8.0
    font = QFont("Arial", 10, QFont.Weight.DemiBold)
    metrics = QFontMetricsF(font)
    text_width = metrics.horizontalAdvance(text) if text else 0.0
    text_height = metrics.height() if text else 0.0
    label_width = max(crosshair_size, (padding * 2) + text_width) if text else 0.0
    label_height = (padding * 2) + text_height if text else 0.0
    width = max(crosshair_size, label_width)
    height = crosshair_size + (label_gap + label_height if text else 0.0)
    image = QImage(
        QSize(int(width * dpr), int(height * dpr)),
        QImage.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(dpr, dpr)

    def _draw_crosshair(center_x: float, center_y: float) -> None:
        """Paint a two-pass crosshair with contrast-friendly strokes."""
        outline_pen = QPen(Qt.GlobalColor.white)
        outline_pen.setWidth(4)
        inner_pen = QPen(Qt.GlobalColor.black)
        inner_pen.setWidth(2)
        gap = 7.0
        size = 18.0
        segments = (
            ((center_x, center_y - size), (center_x, center_y - gap)),
            ((center_x, center_y + gap), (center_x, center_y + size)),
            ((center_x - size, center_y), (center_x - gap, center_y)),
            ((center_x + size, center_y), (center_x + gap, center_y)),
        )
        painter.setPen(outline_pen)
        for start, end in segments:
            painter.drawLine(QPoint(*start), QPoint(*end))
        painter.setPen(inner_pen)
        for start, end in segments:
            painter.drawLine(QPoint(*start), QPoint(*end))

    crosshair_center_x = width / 2.0
    crosshair_center_y = crosshair_size / 2.0
    _draw_crosshair(crosshair_center_x, crosshair_center_y)
    if text:
        label_x = (width - label_width) / 2.0
        label_y = crosshair_size + label_gap
        label_rect = QRectF(label_x, label_y, label_width, label_height)
        painter.setBrush(QColor(20, 20, 20, 220))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(label_rect, 5, 5)
        painter.setFont(font)
        painter.setPen(QColor(245, 245, 245))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    cursor_obj = QCursor(
        pixmap,
        int(crosshair_center_x),
        int(crosshair_center_y),
    )
    cache[cache_key] = cursor_obj
    return cursor_obj
