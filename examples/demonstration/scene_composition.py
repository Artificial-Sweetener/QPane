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

"""Tutorial helpers for catalog-backed scene composition."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter

from qpane import (
    QPane,
    QPaneCatalogImageLayerRequest,
    QPaneScene,
    QPaneSceneOverlayState,
    QPaneSceneRequest,
    QPaneSceneTemplate,
    QPaneSceneTemplateBindings,
    QPaneTemplateLayer,
)


def load_contact_sheet_scene(
    viewer: QPane,
    images: Iterable[QImage],
    *,
    columns: int = 3,
    cell_width: float = 320.0,
    cell_height: float = 240.0,
    gap: float = 16.0,
) -> uuid.UUID:
    """Load images into QPane and display them as a stored contact-sheet scene."""
    image_list = tuple(images)
    image_ids = tuple(uuid.uuid4() for _image in image_list)
    image_map = QPane.imageMapFromLists(image_list, ids=image_ids)
    viewer.setImagesByID(image_map, image_ids[0])
    request = build_contact_sheet_request(
        image_ids,
        image_sizes={
            image_id: image.size()
            for image_id, image in zip(image_ids, image_list, strict=True)
        },
        columns=columns,
        cell_width=cell_width,
        cell_height=cell_height,
        gap=gap,
    )
    return viewer.composeScene(request)


def build_contact_sheet_request(
    image_ids: Sequence[uuid.UUID],
    *,
    image_sizes: Mapping[uuid.UUID, QSize],
    columns: int,
    cell_width: float,
    cell_height: float,
    gap: float,
) -> QPaneSceneRequest:
    """Build a scene request that packs catalog images in thumbnail rows."""
    if columns <= 0:
        raise ValueError("columns must be positive")
    if not image_ids:
        raise ValueError("image_ids must not be empty")
    if cell_width <= 0.0 or cell_height <= 0.0:
        raise ValueError("cell dimensions must be positive")
    if gap < 0.0:
        raise ValueError("gap must be non-negative")
    layers = []
    y = 0.0
    max_row_width = 0.0
    index = 0
    for row_start in range(0, len(image_ids), columns):
        x = 0.0
        row_height = 0.0
        for image_id in image_ids[row_start : row_start + columns]:
            fitted = QPane.fitSceneRect(
                image_sizes[image_id],
                QRectF(0.0, 0.0, cell_width, cell_height),
            )
            placement = QRectF(fitted)
            placement.moveTo(x, y)
            layers.append(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=image_id,
                    placement=placement,
                    role="thumbnail",
                    metadata={"index": index},
                )
            )
            x += placement.width() + gap
            row_height = max(row_height, placement.height())
            index += 1
        max_row_width = max(max_row_width, max(0.0, x - gap))
        y += row_height + gap
    return QPaneSceneRequest(
        composition_id=None,
        title="Contact sheet",
        bounds=QRectF(
            0.0,
            0.0,
            max_row_width,
            max(0.0, y - gap),
        ),
        layers=tuple(layers),
    )


def build_two_up_template() -> QPaneSceneTemplate:
    """Build a reusable two-image template owned by the host application."""
    return QPaneSceneTemplate(
        template_id=uuid.uuid4(),
        title="Two-up",
        bounds=QRectF(0.0, 0.0, 640.0, 320.0),
        layers=(
            QPaneTemplateLayer(
                layer_id=uuid.uuid4(),
                source_slot="left",
                placement=QRectF(0.0, 0.0, 320.0, 320.0),
            ),
            QPaneTemplateLayer(
                layer_id=uuid.uuid4(),
                source_slot="right",
                placement=QRectF(320.0, 0.0, 320.0, 320.0),
            ),
        ),
    )


def compose_two_up_from_template(
    viewer: QPane,
    template: QPaneSceneTemplate,
    left_image_id: uuid.UUID,
    right_image_id: uuid.UUID,
) -> uuid.UUID:
    """Compose and store a two-up scene from a reusable host template."""
    return viewer.composeSceneFromTemplate(
        template,
        QPaneSceneTemplateBindings(
            composition_id=None,
            title="Selected pair",
            catalog_images={"left": left_image_id, "right": right_image_id},
        ),
    )


def reopen_scene(viewer: QPane, composition_id: uuid.UUID) -> QPaneScene | None:
    """Open a stored scene composition and return QPane's normalized snapshot."""
    viewer.openComposition(composition_id)
    return viewer.currentScene()


def draw_contact_sheet_labels(
    painter: QPainter,
    state: QPaneSceneOverlayState,
) -> None:
    """Draw simple labels over active layered scene composition layers."""
    painter.setPen(Qt.white)
    for layer in state.layers:
        label = f"{layer.role} {layer.metadata.get('index', '')}".strip()
        painter.drawText(layer.panel_bounds.adjusted(8, 8, -8, -8), label)


def install_contact_sheet_overlay(viewer: QPane) -> None:
    """Register the tutorial contact-sheet scene overlay."""
    viewer.unregisterSceneOverlay("contact_sheet_labels")
    viewer.registerSceneOverlay("contact_sheet_labels", draw_contact_sheet_labels)


def remove_contact_sheet_overlay(viewer: QPane) -> None:
    """Remove the tutorial contact-sheet scene overlay."""
    viewer.unregisterSceneOverlay("contact_sheet_labels")


def navigate_contact_sheet_hit(viewer: QPane, panel_pos: QPoint) -> uuid.UUID | None:
    """Navigate to the catalog image under ``panel_pos`` when a scene layer is hit."""
    hit = viewer.sceneHitTest(panel_pos)
    if hit is None:
        return None
    viewer.setCurrentImageID(hit.image_id)
    return hit.image_id
