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

"""Drag-out helpers for QPane widgets."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDrag, QGuiApplication, QImage, QMouseEvent, QPixmap

if TYPE_CHECKING:
    from ..qpane import QPane
logger = logging.getLogger(__name__)


def maybeStartDrag(
    qpane: "QPane",
    event: QMouseEvent | None,
    *,
    image: QImage | None = None,
    path: Path | None = None,
) -> None:
    """Start an OS-level drag for the qpane's current image once prerequisites are met.

    Args:
        qpane: QPane initiating the drag.
        event: Mouse event forwarded from Qt; accepted only to match the slot signature.
        image: Base image payload used for the drag preview.
        path: Filesystem path offered to the OS drag target.

    Side effects:
        Emits a ``QDrag`` with the current image file so external apps can receive the payload.
        Generates a preview pixmap sized to roughly 15% of the active screen for continuity with the live view.
    Notes:
        The camelCase name aligns with Qt signal wiring (``drag_start_maybe_requested``), so callers do not need shims.
    """
    del event
    current_path = path if path is not None else qpane.currentImagePath
    if current_path is None:
        logger.warning("maybeStartDrag aborted: current image path is missing.")
        return
    if not isinstance(current_path, Path):
        current_path = Path(current_path)
    if not current_path.exists():
        logger.warning(
            "maybeStartDrag aborted: drag source path does not exist (%s).",
            current_path,
        )
        return
    preview_image = image if image is not None else qpane.currentImage
    if preview_image is None or preview_image.isNull():
        logger.warning(
            "maybeStartDrag aborted: current image is null for %s.",
            current_path,
        )
        return
    screen = qpane.screen() if hasattr(qpane, "screen") else None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        logger.warning(
            "maybeStartDrag aborted: no screen available to size the drag preview."
        )
        return
    screen_size = screen.availableGeometry().size()
    max_drag_width = int(screen_size.width() * 0.15)
    max_drag_height = int(screen_size.height() * 0.15)
    preview_pixmap = QPixmap.fromImage(preview_image).scaled(
        max_drag_width,
        max_drag_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    drag = QDrag(qpane)
    mime_data = QMimeData()
    file_url = QUrl.fromLocalFile(str(current_path))
    mime_data.setUrls([file_url])
    drag.setMimeData(mime_data)
    drag.setPixmap(preview_pixmap)
    drag.exec(Qt.DropAction.CopyAction)
