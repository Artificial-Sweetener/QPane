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

from dataclasses import replace
import math
import uuid

import pytest
from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QImage, QPixmap, Qt

from qpane import QPane
from qpane.rendering.render import Renderer
from qpane.scene.identity import mask_layer_asset_key
from qpane.scene.model import ClipCoordinateSpace, LayerClip, LayerKind
from qpane.scene.render_plan import MaskLayerRenderItem
from qpane.scene.sources import MaskLayerSource
from tests.helpers.render_plan import make_render_plan


@pytest.fixture()
def qpane_with_image(qapp):
    qpane = QPane(features=())
    qpane.resize(128, 128)
    image = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.black)
    image_id = uuid.uuid4()
    image_map = QPane.imageMapFromLists([image], [None], [image_id])
    qpane.setImagesByID(image_map, image_id)
    yield qpane
    qpane.deleteLater()
    qapp.processEvents()


def _make_mask_plan(qpane_rect: QRect):
    """Return a base raster plan with a mask render item appended."""
    plan = make_render_plan(qpane_rect)
    base_item = plan.base_raster_item
    assert base_item is not None
    mask_id = uuid.uuid4()
    mask_descriptor = replace(
        base_item.descriptor,
        layer_id=uuid.uuid4(),
        kind=LayerKind.MASK,
        source=MaskLayerSource(mask_id=mask_id, revision=0),
    )
    mask_item = MaskLayerRenderItem(
        descriptor=mask_descriptor,
        pixmap=QPixmap.fromImage(base_item.source_image),
        asset_key=mask_layer_asset_key(
            scene_id=plan.scene_id,
            mask_id=mask_id,
            revision=0,
        ),
        transform=base_item.transform,
        placement=base_item.placement,
        clip=None,
        render_hint_enabled=False,
        scale=1.0,
    )
    return replace(plan, render_items=(base_item, mask_item)), mask_item


def test_try_scroll_buffers_uses_qpane_render_plan(qpane_with_image, monkeypatch):
    qpane = qpane_with_image
    view = qpane.view()
    renderer = view.renderer
    renderer._buffer_pan = QPointF(0.0, 0.0)
    renderer._subpixel_pan_offset = QPointF(0.0, 0.0)
    repair_calls = {}

    def fake_repair(rects, state):
        repair_calls["rects"] = rects
        repair_calls["state"] = state

    monkeypatch.setattr(renderer, "_repair_base_buffer_strips", fake_repair)
    captured = {}
    original_calculate = view.calculateRenderPlan

    def fake_calculate(use_pan=None, **kwargs):
        captured["use_pan"] = use_pan
        return original_calculate(use_pan=use_pan, **kwargs)

    monkeypatch.setattr(view, "calculateRenderPlan", fake_calculate)
    new_pan = QPointF(4.0, 3.0)
    view.viewport.pan = QPointF(new_pan)
    result = renderer.tryScrollBuffers(new_pan)
    assert result is True
    assert captured["use_pan"] == renderer._buffer_pan
    assert "rects" in repair_calls and repair_calls["rects"]
    assert repair_calls["state"] is not None


def test_base_scroll_strip_repair_uses_direct_fast_path(
    qpane_with_image,
    monkeypatch,
) -> None:
    """Base-only strip repair should not route through generic scene drawing."""
    qpane = qpane_with_image
    renderer = qpane.view().renderer
    plan = qpane.view().calculateRenderPlan(is_blank=False)
    assert plan is not None
    calls = []

    def fail_generic_draw(*_args, **_kwargs):
        raise AssertionError("base strip repair should not draw the whole scene")

    monkeypatch.setattr(renderer, "_draw_visible_scene_items", fail_generic_draw)
    monkeypatch.setattr(
        renderer,
        "_repair_base_raster_strips_directly",
        lambda rects, repair_plan: calls.append((rects, repair_plan)),
    )

    renderer._repair_base_buffer_strips([qpane.rect()], plan)

    assert calls == [([qpane.rect()], plan)]


def test_base_only_predicate_accepts_default_raster_plan() -> None:
    """A single full-scene base raster item should qualify for fast paths."""
    plan = make_render_plan(QRect(0, 0, 64, 64))

    assert plan.base_raster_item is not None

    assert Renderer._base_only_raster_item(plan) is plan.base_raster_item


def test_base_only_predicate_rejects_mask_plan() -> None:
    """Mask render items must keep the layered renderer path."""
    mask_plan, _mask_item = _make_mask_plan(QRect(0, 0, 64, 64))

    assert Renderer._base_only_raster_item(mask_plan) is None


def test_base_only_predicate_rejects_clipped_plan() -> None:
    """Clipped reveal/comparison layers must keep visibility-aware rendering."""
    plan = make_render_plan(QRect(0, 0, 64, 64))
    base_item = plan.base_raster_item
    assert base_item is not None
    clip = LayerClip(
        coordinate_space=ClipCoordinateSpace.NORMALIZED_VIEWPORT,
        x=0.0,
        y=0.0,
        width=0.5,
        height=1.0,
    )
    clipped_item = replace(
        base_item,
        descriptor=replace(base_item.descriptor, clip=clip),
        clip=clip,
    )
    clipped_plan = replace(plan, render_items=(clipped_item,))

    assert Renderer._base_only_raster_item(clipped_plan) is None


def test_base_only_predicate_rejects_multi_image_plan() -> None:
    """Additional raster layers must keep the general scene renderer."""
    plan = make_render_plan(QRect(0, 0, 64, 64))
    base_item = plan.base_raster_item
    assert base_item is not None
    additional_item = replace(
        base_item,
        descriptor=replace(base_item.descriptor, layer_id=uuid.uuid4()),
    )
    layered_plan = replace(plan, render_items=(base_item, additional_item))

    assert Renderer._base_only_raster_item(layered_plan) is None


def test_base_dirty_redraw_uses_direct_fast_path(
    qpane_with_image,
    monkeypatch,
) -> None:
    """Base-only dirty redraw should not route through generic scene drawing."""
    qpane = qpane_with_image
    renderer = qpane.view().renderer
    plan = qpane.view().calculateRenderPlan(is_blank=False)
    assert plan is not None
    assert plan.base_raster_item is not None
    direct_calls = []

    def fail_generic_draw(*_args, **_kwargs):
        raise AssertionError("base dirty redraw should not draw the whole scene")

    monkeypatch.setattr(renderer, "_draw_visible_scene_items", fail_generic_draw)
    monkeypatch.setattr(
        renderer,
        "_draw_direct_view",
        lambda painter, item: direct_calls.append(item),
    )

    renderer.markDirty(qpane.rect())
    renderer.paint(plan)

    assert direct_calls == [plan.base_raster_item]


def test_layered_strip_repair_draws_mask_source_strips(
    qpane_with_image,
    monkeypatch,
) -> None:
    """Layered mask strip repair should avoid full-pixmap redraw when possible."""
    qpane = qpane_with_image
    renderer = qpane.view().renderer
    plan, mask_item = _make_mask_plan(qpane.rect())
    repair_rects = [QRect(0, 0, 16, 16)]
    mask_calls = []

    monkeypatch.setattr(
        renderer,
        "_draw_raster_source_strips",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        renderer,
        "_draw_pixmap_source_strips",
        lambda painter, item, rects, context: mask_calls.append((item, rects)) or True,
    )

    assert renderer._repair_layered_strips(repair_rects, plan) is True

    assert mask_calls == [(mask_item, repair_rects)]


def test_try_scroll_buffers_tracks_fractional_offsets(qpane_with_image, monkeypatch):
    qpane = qpane_with_image
    view = qpane.view()
    renderer = view.renderer
    renderer._buffer_pan = QPointF(0.0, 0.0)
    monkeypatch.setattr(
        renderer, "_repair_base_buffer_strips", lambda rects, state: None
    )
    fractional_pan = QPointF(7.75, 2.5)
    view.viewport.pan = QPointF(fractional_pan)
    result = renderer.tryScrollBuffers(fractional_pan)
    assert result is True
    offset = renderer.get_subpixel_pan_offset()
    expected_offset = view.viewport.pan - renderer._buffer_pan
    assert math.isclose(offset.x(), expected_offset.x(), abs_tol=1e-6)
    assert math.isclose(offset.y(), expected_offset.y(), abs_tol=1e-6)


def test_try_scroll_buffers_rejects_large_scroll(qpane_with_image):
    qpane = qpane_with_image
    renderer = qpane.view().renderer
    renderer._buffer_pan = QPointF(0.0, 0.0)
    large_pan = QPointF(renderer._base_image_buffer.width() * 2, 0.0)
    result = renderer.tryScrollBuffers(large_pan)
    assert result is False
    assert renderer._buffer_pan == QPointF(0.0, 0.0)


def test_try_scroll_buffers_requires_buffer(qapp):
    qpane = QPane(features=())
    try:
        renderer = qpane.view().renderer
        renderer._base_image_buffer = None
        result = renderer.tryScrollBuffers(QPointF(1.0, 1.0))
    finally:
        qpane.deleteLater()
        qapp.processEvents()
    assert result is False
