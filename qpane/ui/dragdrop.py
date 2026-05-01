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

"""Drag-and-drop utilities consumed by QPane and catalog controllers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, QSizeF
from PySide6.QtGui import QImage, QMouseEvent

from ..rendering import ViewportZoomMode
from .dragout import maybeStartDrag

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..qpane import QPane


def drag_out_image(
    qpane: "QPane",
    event: QMouseEvent | None,
    *,
    image: QImage | None = None,
    path: Path | None = None,
) -> None:
    """Forward Qt drag requests to the drag-out helper while preserving the signature.

    Args:
        qpane: QPane whose current image should be offered to the OS drag target.
        event: Mouse event forwarded from Qt; accepted only to match the slot signature.
        image: Optional base image used for the drag preview.
        path: Optional filesystem path offered to the OS drag target.
    """
    if not getattr(qpane.settings, "drag_out_enabled", True):
        return
    maybeStartDrag(qpane, event, image=image, path=path)


def is_drag_out_allowed(
    *,
    image_size: QSize | QSizeF,
    zoom: float,
    zoom_mode: ViewportZoomMode,
    viewport_size: QSizeF,
) -> bool:
    """Return True when drag-out gestures keep the content within the viewport.

    Args:
        image_size: Size of the base image that may be dragged out.
        zoom: Current zoom factor applied to ``image_size``.
        zoom_mode: Active zoom policy controlling fit vs. manual zoom.
        viewport_size: Visible viewport dimensions expressed in device pixels.

    Returns:
        True when the scaled image fits inside the viewport or zoom-fit mode is active.
    """
    del zoom_mode
    if image_size.width() <= 0 or image_size.height() <= 0:
        return False
    scaled_width = image_size.width() * zoom
    scaled_height = image_size.height() * zoom
    return (
        scaled_width <= viewport_size.width()
        and scaled_height <= viewport_size.height()
    )
