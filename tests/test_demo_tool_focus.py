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

"""Demo window tests covering tool focus during catalog navigation."""

import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from examples.demo import ExampleOptions, ExampleWindow
from qpane import QPane


def _solid_image() -> QImage:
    """Return a tiny image for demo tool focus tests."""
    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(Qt.black)
    return image


def test_demo_catalog_clicks_drive_tool_focus(qapp) -> None:
    """Ensure catalog selections set the expected tool modes."""
    window = ExampleWindow(ExampleOptions(feature_set="masksam"))
    try:
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists([_solid_image()], [None], [image_id])
        window.qpane.setImagesByID(image_map, current_id=image_id)
        image = window.qpane.currentImage
        assert image is not None
        mask_id = window.qpane.createBlankMask(image.size())
        assert mask_id is not None
        window.qpane.setActiveMaskID(mask_id)
        window.qpane.setCurrentImageID(None)
        qapp.processEvents()

        assert window.catalog_dock is not None
        window.catalog_dock._catalog_action.setChecked(True)
        qapp.processEvents()

        def _items():
            """Return the current catalog tree items after refreshes."""
            tree = window.catalog_dock._tree
            return tree._mask_items[(image_id, mask_id)], tree._image_items[image_id]

        window._set_control_mode(QPane.CONTROL_MODE_CURSOR)
        mask_item, image_item = _items()
        window.catalog_dock._handle_item_clicked(mask_item, 0)
        qapp.processEvents()
        assert window.qpane.getControlMode() == QPane.CONTROL_MODE_DRAW_BRUSH

        window._set_control_mode(QPane.CONTROL_MODE_SMART_SELECT)
        mask_item, image_item = _items()
        window.catalog_dock._handle_item_clicked(mask_item, 0)
        qapp.processEvents()
        assert window.qpane.getControlMode() == QPane.CONTROL_MODE_SMART_SELECT

        window._set_control_mode(QPane.CONTROL_MODE_DRAW_BRUSH)
        mask_item, image_item = _items()
        window.catalog_dock._handle_item_clicked(image_item, 0)
        qapp.processEvents()
        assert window.qpane.getControlMode() == QPane.CONTROL_MODE_PANZOOM
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
