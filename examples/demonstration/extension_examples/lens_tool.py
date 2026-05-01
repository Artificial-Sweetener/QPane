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

"""Cursor + overlay lens example that magnifies under the pointer.

NOTE: This file is designed for the QPane Interactive Demo playground.
It uses standalone functions to allow hot-reloading of logic without
re-registering the tool class itself.

For a production implementation, prefer the class-based approach
described in `docs/extensibility.md`, where `draw_overlay` and
`getCursor` are methods of your `ExtensionTool` subclass.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QCursor, QImage, QPainterPath, QPen, QPixmap


# Provided by the demo host at execution time:
# - CUSTOM_MODE: str (the tool mode this lens belongs to)
# - qpane: QPane (host widget)
CUSTOM_MODE = globals().get("CUSTOM_MODE")
qpane = globals().get("qpane")


_VIEWPORT_CACHE = {"rect": None}


def _capture_viewport(rect: QRectF) -> None:
    """Cache the viewport rectangle for quick access."""
    _VIEWPORT_CACHE["rect"] = rect


def _qpane_viewport_rect():
    """Return the cached viewport rectangle as a QRect when available."""
    rect = _VIEWPORT_CACHE.get("rect")
    if rect is None:
        try:
            rect = qpane.currentViewportRect()
        except Exception:
            return None
        _VIEWPORT_CACHE["rect"] = rect
    if hasattr(rect, "toRect"):
        return rect.toRect()
    return QRect(
        int(rect.left()),
        int(rect.top()),
        int(rect.width()),
        int(rect.height()),
    )


if not globals().get("_LENS_VIEWPORT_CONNECTED"):
    try:
        qpane.viewportRectChanged.connect(_capture_viewport)
        _VIEWPORT_CACHE["rect"] = qpane.currentViewportRect()
        globals()["_LENS_VIEWPORT_CONNECTED"] = True
    except Exception:
        globals()["_LENS_VIEWPORT_CONNECTED"] = False


def cursor(qpane):
    """Return a transparent cursor registered via QPane.registerCursorProvider.

    QPane calls this hook for cursor updates; return None when the tool is inactive.
    """
    if qpane.getControlMode() != CUSTOM_MODE:
        return None
    viewport_rect = _qpane_viewport_rect()
    if viewport_rect is not None:
        cursor_pos = qpane.mapFromGlobal(QCursor.pos())
        if not viewport_rect.contains(cursor_pos):
            return None
    pixmap = QPixmap(1, 1)
    pixmap.fill(Qt.GlobalColor.transparent)
    return QCursor(pixmap)


def draw_overlay(painter, state):
    """Paint the lens overlay registered via QPane.registerOverlay.

    QPane calls this hook during overlay repaints; exit early when inactive.
    """
    image = state.source_image
    if qpane.getControlMode() != CUSTOM_MODE or image.isNull():
        return
    if state.zoom >= 1.0:
        painter.save()
        cursor_pos = qpane.mapFromGlobal(QCursor.pos())
        pen = QPen(Qt.GlobalColor.white, 2)
        painter.setPen(pen)
        size = 12
        painter.drawLine(
            cursor_pos.x() - size, cursor_pos.y(), cursor_pos.x() + size, cursor_pos.y()
        )
        painter.drawLine(
            cursor_pos.x(),
            cursor_pos.y() - size,
            cursor_pos.x(),
            cursor_pos.y() + size,
        )
        painter.restore()
        return
    painter.save()
    lens_radius = 96
    lens_diameter = lens_radius * 2
    dpr = qpane.devicePixelRatioF()
    sample_size = int(lens_diameter * dpr)
    cursor_pos = qpane.mapFromGlobal(QCursor.pos())
    viewport_rect = _qpane_viewport_rect()
    if viewport_rect is not None and not viewport_rect.contains(cursor_pos):
        painter.restore()
        return
    hit = qpane.panelHitTest(cursor_pos)
    if hit is None or not getattr(hit, "inside_image", False):
        painter.save()
        pen = QPen(Qt.GlobalColor.white, 2)
        painter.setPen(pen)
        size = 12
        painter.drawLine(
            cursor_pos.x() - size, cursor_pos.y(), cursor_pos.x() + size, cursor_pos.y()
        )
        painter.drawLine(
            cursor_pos.x(),
            cursor_pos.y() - size,
            cursor_pos.x(),
            cursor_pos.y() + size,
        )
        painter.restore()
        painter.restore()
        return
    image_point = hit.clamped_point
    target_left = image_point.x() - (sample_size // 2)
    target_top = image_point.y() - (sample_size // 2)
    src_rect = QRect(target_left, target_top, sample_size, sample_size).intersected(
        image.rect()
    )
    if src_rect.isEmpty():
        painter.restore()
        return
    region: QImage = image.copy(src_rect)
    full_dest = QRect(
        cursor_pos.x() - lens_radius,
        cursor_pos.y() - lens_radius,
        lens_diameter,
        lens_diameter,
    )
    trim_x = src_rect.left() - target_left
    trim_y = src_rect.top() - target_top
    dest_size = QSize(
        max(1, int(src_rect.width() / dpr)), max(1, int(src_rect.height() / dpr))
    )
    dest_top_left = full_dest.topLeft() + QPoint(int(trim_x / dpr), int(trim_y / dpr))
    clip_path = QPainterPath()
    clip_path.addEllipse(full_dest)
    painter.setClipPath(clip_path)
    painter.drawImage(
        QRect(dest_top_left, dest_size),
        region,
        QRect(0, 0, region.width(), region.height()),
    )
    painter.setClipping(False)
    pen = QPen(QColor(255, 99, 71), 2)
    painter.setPen(pen)
    painter.drawEllipse(full_dest)
    painter.restore()
