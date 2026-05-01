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

"""CatalogController integration tests around batch removal."""

import uuid
from pathlib import Path
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, Qt
from qpane.types import CatalogEntry
from qpane.rendering import ViewportZoomMode


def _solid_image(color: Qt.GlobalColor, size: int = 8) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def test_remove_images_by_id_batches_evictions_and_display(monkeypatch, qpane_view):
    controller = qpane_view.catalog_controller
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    image_map = {
        first: CatalogEntry(image=_solid_image(Qt.red), path=Path("a.png")),
        second: CatalogEntry(image=_solid_image(Qt.green), path=Path("b.png")),
        third: CatalogEntry(image=_solid_image(Qt.blue), path=Path("c.png")),
    }
    controller.setImagesByID(image_map, first, display=False)
    display_calls: list[bool] = []
    monkeypatch.setattr(
        controller,
        "_display_current_catalog_image",
        lambda *, fit_view=True: display_calls.append(fit_view),
    )
    evictions: list[tuple[uuid.UUID, ...]] = []
    sam_evictions: list[tuple[uuid.UUID, ...]] = []
    monkeypatch.setattr(
        controller,
        "_evict_catalog_assets",
        lambda asset_keys: evictions.append(
            tuple(asset_key.source_id for asset_key in asset_keys)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_evict_sam_images",
        lambda image_ids: sam_evictions.append(tuple(image_ids)),
    )
    removed = controller.removeImagesByID([second, first, second])
    assert removed == (second, first)
    assert display_calls == [True]
    assert len(evictions) == 1
    assert set(evictions[0]) == {first, second}
    assert len(sam_evictions) == 1
    assert set(sam_evictions[0]) == {first, second}
    assert controller.catalog.getImageIds() == [third]


def test_set_images_by_id_evicts_removed_and_changed_ids(monkeypatch, qpane_view):
    controller = qpane_view.catalog_controller
    unchanged = uuid.uuid4()
    changed = uuid.uuid4()
    removed = uuid.uuid4()
    controller.setImagesByID(
        {
            unchanged: CatalogEntry(image=_solid_image(Qt.red), path=Path("a.png")),
            changed: CatalogEntry(image=_solid_image(Qt.green), path=Path("b.png")),
            removed: CatalogEntry(image=_solid_image(Qt.blue), path=Path("c.png")),
        },
        unchanged,
        display=False,
    )
    asset_evictions: list[tuple[uuid.UUID, ...]] = []
    sam_evictions: list[tuple[uuid.UUID, ...]] = []
    monkeypatch.setattr(
        controller,
        "_evict_catalog_assets",
        lambda asset_keys: asset_evictions.append(
            tuple(asset_key.source_id for asset_key in asset_keys)
        ),
    )
    monkeypatch.setattr(
        controller,
        "_evict_sam_images",
        lambda image_ids: sam_evictions.append(tuple(image_ids)),
    )
    controller.setImagesByID(
        {
            unchanged: CatalogEntry(image=_solid_image(Qt.red), path=Path("a2.png")),
            changed: CatalogEntry(image=_solid_image(Qt.white), path=Path("b.png")),
        },
        changed,
        display=False,
    )
    assert len(asset_evictions) == 1
    assert set(asset_evictions[0]) == {unchanged, changed, removed}
    assert len(sam_evictions) == 1
    assert set(sam_evictions[0]) == {changed, removed}


def test_restore_view_state_preserves_pan_in_1to1_mode(qpane_view):
    """Ensure pan is restored when switching back to an image in 1:1 mode."""
    controller = qpane_view.catalog_controller
    qpane = qpane_view._qpane
    qpane.resize(200, 200)

    # Setup
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()

    # Create images larger than default viewport to allow panning
    img1 = _solid_image(Qt.red, size=400)
    img2 = _solid_image(Qt.blue, size=400)

    image_map = {
        id1: CatalogEntry(image=img1, path=Path("img1.png")),
        id2: CatalogEntry(image=img2, path=Path("img2.png")),
    }

    controller.setImagesByID(image_map, id1)

    # Set 1:1 mode and pan
    qpane.setZoom1To1()
    target_pan = QPointF(50, 50)
    qpane.setPan(target_pan)

    # Verify initial state
    viewport = qpane.view().viewport
    assert viewport.get_zoom_mode() == ViewportZoomMode.ONE_TO_ONE
    assert viewport.pan == target_pan

    # Switch away and back
    controller.setCurrentImageID(id2)
    controller.setCurrentImageID(id1)

    # Verify restored state
    assert viewport.get_zoom_mode() == ViewportZoomMode.ONE_TO_ONE
    assert viewport.pan == target_pan
