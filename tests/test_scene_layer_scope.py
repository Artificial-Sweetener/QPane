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

"""Tests for the current shipped scene-layer domain surface."""

from __future__ import annotations

import uuid

from PySide6.QtGui import QImage, Qt

from qpane import QPane
from qpane.scene.model import LayerKind


def _solid_image() -> QImage:
    """Return a small solid image for scene resolution tests."""
    image = QImage(16, 16, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.white)
    return image


def test_qpane_does_not_register_removed_layer_domain_services(qapp) -> None:
    """QPane should not expose removed private layer-domain services."""
    qpane = QPane(features=())
    try:
        assert not hasattr(qpane, "adjustmentService")
        assert not hasattr(qpane, "editableRasterService")
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_current_product_scene_resolution_omits_removed_layer_domains(qapp) -> None:
    """Current product scenes should not resolve removed concrete layer domains."""
    qpane = QPane(features=())
    try:
        image_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists([_solid_image()], [None], [image_id]),
            image_id,
        )

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        resolved_kinds = {item.descriptor.kind.value for item in plan.render_items}
        assert "adjustment" not in resolved_kinds
        assert "editable-raster" not in resolved_kinds
        assert "adjustment" not in {kind.value for kind in LayerKind}
        assert "editable-raster" not in {kind.value for kind in LayerKind}
    finally:
        qpane.deleteLater()
        qapp.processEvents()
