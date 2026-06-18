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

"""Pixel comparison helpers for render-buffer regression tests."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter


def assert_images_match(
    actual: QImage,
    expected: QImage,
    *,
    tolerance: int = 0,
) -> None:
    """Assert that two images have matching pixels within ``tolerance``.

    The default is exact equality. A small tolerance can be used for tests that
    intentionally compare paths involving Qt filtering or fractional device-pixel
    placement, where harmless one-channel rounding differences may appear.
    """
    assert actual.size() == expected.size()
    assert actual.format() == expected.format()
    for y in range(actual.height()):
        for x in range(actual.width()):
            actual_color = actual.pixelColor(x, y)
            expected_color = expected.pixelColor(x, y)
            deltas = (
                abs(actual_color.red() - expected_color.red()),
                abs(actual_color.green() - expected_color.green()),
                abs(actual_color.blue() - expected_color.blue()),
                abs(actual_color.alpha() - expected_color.alpha()),
            )
            assert max(deltas) <= tolerance, (
                f"pixel mismatch at ({x}, {y}): "
                f"{actual_color.getRgb()} != {expected_color.getRgb()}"
            )


def checker_image(size: QSize) -> QImage:
    """Return a deterministic high-contrast image for pan/repair comparisons."""
    image = QImage(size, QImage.Format_ARGB32_Premultiplied)
    for y in range(size.height()):
        for x in range(size.width()):
            band = ((x // 8) + (y // 8)) % 2
            red = (x * 5 + y * 3) % 256
            green = (x * 11 + y * 7) % 256
            blue = 230 if band else 35
            image.setPixelColor(x, y, QColor(red, green, blue, 255))
    return image


def rendered_widget_frame(base_buffer: QImage, subpixel_offset: QPointF) -> QImage:
    """Return the widget-visible frame after applying renderer subpixel offset."""
    frame = QImage(base_buffer.size(), QImage.Format_ARGB32_Premultiplied)
    frame.setDevicePixelRatio(base_buffer.devicePixelRatio())
    frame.fill(0)
    painter = QPainter(frame)
    try:
        if subpixel_offset != QPointF(0.0, 0.0):
            dpr = base_buffer.devicePixelRatio()
            safe_dpr = dpr if dpr > 0 else 1.0
            painter.translate(subpixel_offset / safe_dpr)
        painter.drawImage(0, 0, base_buffer)
    finally:
        painter.end()
    return frame


def rendered_overscanned_widget_frame(
    base_buffer: QImage,
    subpixel_offset: QPointF,
    viewport_size: QSize,
    overscan_margin: int,
) -> QImage:
    """Return the widget-visible crop from an overscanned renderer buffer."""
    frame = QImage(viewport_size, QImage.Format_ARGB32_Premultiplied)
    frame.setDevicePixelRatio(base_buffer.devicePixelRatio())
    frame.fill(0)
    dpr = base_buffer.devicePixelRatio()
    safe_dpr = dpr if dpr > 0 else 1.0
    painter = QPainter(frame)
    try:
        source_rect = QRectF(
            overscan_margin - subpixel_offset.x(),
            overscan_margin - subpixel_offset.y(),
            float(viewport_size.width()),
            float(viewport_size.height()),
        )
        destination_rect = QRectF(
            0.0,
            0.0,
            viewport_size.width() / safe_dpr,
            viewport_size.height() / safe_dpr,
        )
        painter.drawImage(destination_rect, base_buffer, source_rect)
    finally:
        painter.end()
    return frame
