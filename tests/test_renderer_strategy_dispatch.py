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

from __future__ import annotations

import types
import uuid

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QImage, QRegion, Qt, QTransform

from qpane import QPane
from qpane.rendering import Renderer
from qpane.scene.render_plan import RenderStrategy
from tests.helpers.render_plan import make_render_plan


class _StubQPane:
    def __init__(self, size: QSize):
        self._size = size
        self.viewport = types.SimpleNamespace(zoom=1.0, pan=QPointF(0.0, 0.0))
        self.original_image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        self.original_image.fill(Qt.white)

    def devicePixelRatioF(self) -> float:
        return 1.0

    def size(self) -> QSize:
        return self._size


def _make_plan(qpane_rect: QRect, strategy: RenderStrategy):
    source_image = QImage(qpane_rect.size(), QImage.Format_ARGB32_Premultiplied)
    source_image.fill(Qt.white)
    return make_render_plan(
        qpane_rect,
        source_image=source_image,
        transform=QTransform(),
        strategy=strategy,
        current_pan=QPointF(0.0, 0.0),
        physical_viewport_rect=QRectF(qpane_rect),
        visible_tile_range=(0, 0, 0, 0) if strategy is RenderStrategy.TILE else None,
    )


@pytest.mark.parametrize(
    "strategy, expected_direct_calls, expected_tile_calls",
    [
        (RenderStrategy.DIRECT, 1, 0),
        (RenderStrategy.TILE, 0, 1),
    ],
)
def test_redraw_base_image_buffer_respects_strategy(
    monkeypatch, strategy, expected_direct_calls, expected_tile_calls
):
    qpane_rect = QRect(0, 0, 40, 40)
    qpane = _StubQPane(qpane_rect.size())
    renderer = Renderer(qpane)
    renderer._base_image_buffer = QImage(
        qpane_rect.size(), QImage.Format_ARGB32_Premultiplied
    )
    renderer._base_image_buffer.fill(Qt.transparent)
    plan = _make_plan(qpane_rect, strategy=strategy)
    dirty_region = QRegion(qpane_rect)
    direct_calls = []
    tile_calls = []
    monkeypatch.setattr(
        renderer,
        "_draw_direct_view",
        lambda painter, item: direct_calls.append(item),
    )
    monkeypatch.setattr(
        renderer,
        "_draw_tiled_view",
        lambda painter, plan, item: tile_calls.append((plan, item)),
    )
    renderer._redraw_base_image_buffer(dirty_region, plan)
    assert len(direct_calls) == expected_direct_calls
    assert len(tile_calls) == expected_tile_calls


def test_calculate_render_plan_prefers_direct_when_image_fits(qapp):
    qpane = QPane(features=())
    try:
        qpane.resize(256, 256)
        image = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.black)
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists([image], [None], [image_id])
        qpane.setImagesByID(image_map, image_id)
        # Ensure fit_view=False behavior by manually resetting zoom if needed,
        # but setImagesByID might fit view by default.
        # The original test passed fit_view=False.
        # setImagesByID doesn't take fit_view. It usually resets view.
        # We might need to manually set zoom to 1.0 or whatever fit_view=False implied.
        # fit_view=False usually means "don't change zoom" or "set to 1.0"?
        # Actually, setImagesByID calls catalog.setImagesByID which calls displayCurrentCatalogImage(fit_view=True).
        # So it forces fit_view=True.
        # If we want to test specific zoom behavior, we should set the zoom after loading.
        viewport = qpane.view().viewport
        viewport.setZoomFit()  # The original test called this anyway!
        plan = qpane.view().calculateRenderPlan()
    finally:
        qpane.deleteLater()
        qapp.processEvents()
    assert plan is not None
    item = plan.base_raster_item
    assert item is not None
    assert item.strategy is RenderStrategy.DIRECT


def test_calculate_render_plan_switches_to_tile_for_large_zoom(qapp):
    qpane = QPane(features=())
    try:
        qpane.resize(128, 128)
        image = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.black)
        image_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists([image], [None], [image_id])
        qpane.setImagesByID(image_map, image_id)
        viewport = qpane.view().viewport
        viewport.setZoomFit()
        viewport.applyZoom(4.0)
        plan = qpane.view().calculateRenderPlan()
    finally:
        qpane.deleteLater()
        qapp.processEvents()
    assert plan is not None
    item = plan.base_raster_item
    assert item is not None
    assert item.strategy is RenderStrategy.TILE
