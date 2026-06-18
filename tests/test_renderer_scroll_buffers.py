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
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QImage, QPixmap, Qt

from qpane import (
    QPane,
    QPaneCatalogImageLayerRequest,
    QPaneSceneClip,
    QPaneSceneRequest,
)
from qpane.rendering.render import Renderer
from qpane.scene.identity import mask_layer_asset_key
from qpane.scene.model import ClipCoordinateSpace, LayerClip, LayerKind
from qpane.scene.render_plan import (
    MaskLayerRenderItem,
    RenderStrategy,
    TileRenderData,
)
from qpane.scene.sources import MaskLayerSource
from tests.helpers.render_compare import (
    assert_images_match,
    checker_image,
    rendered_overscanned_widget_frame,
)
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


def _render_clean_frame(qpane: QPane, pan: QPointF) -> QImage:
    """Return a full-redraw frame for ``pan`` using the live renderer."""
    view = qpane.view()
    renderer = view.renderer
    view.allocate_buffers()
    view.viewport.pan = QPointF(pan)
    renderer.markDirty()
    plan = view.calculateRenderPlan(use_pan=pan, is_blank=False)
    assert plan is not None
    renderer.paint(plan)
    buffer = renderer.get_base_buffer()
    assert buffer is not None
    return rendered_overscanned_widget_frame(
        QImage(buffer),
        renderer.get_subpixel_pan_offset(),
        renderer._viewport_physical_size,
        renderer._BUFFER_OVERSCAN_PHYSICAL_PX,
    )


def _render_scrolled_frame(
    qpane: QPane,
    *,
    start_pan: QPointF,
    target_pan: QPointF,
) -> QImage:
    """Return a frame produced by scroll-buffer repair from start to target pan."""
    view = qpane.view()
    renderer = view.renderer
    view.allocate_buffers()
    view.viewport.pan = QPointF(start_pan)
    renderer.markDirty()
    start_plan = view.calculateRenderPlan(use_pan=start_pan, is_blank=False)
    assert start_plan is not None
    renderer.paint(start_plan)
    view.viewport.pan = QPointF(target_pan)
    assert renderer.tryScrollBuffers(target_pan) is True
    buffer = renderer.get_base_buffer()
    assert buffer is not None
    return rendered_overscanned_widget_frame(
        QImage(buffer),
        renderer.get_subpixel_pan_offset(),
        renderer._viewport_physical_size,
        renderer._BUFFER_OVERSCAN_PHYSICAL_PX,
    )


def _make_qpane_with_checker_image(
    qapp,
    *,
    size: int = 256,
    dpr: float = 1.0,
) -> QPane:
    """Return a QPane containing one high-contrast image."""
    qpane = QPane(features=())
    qpane.resize(128, 128)
    qpane.devicePixelRatioF = lambda: dpr  # type: ignore[method-assign]
    image = checker_image(QRect(0, 0, size, size).size())
    image_id = uuid.uuid4()
    qpane.setImagesByID(QPane.imageMapFromLists([image], [None], [image_id]), image_id)
    qpane.setZoom1To1()
    qapp.processEvents()
    return qpane


def _assert_edges_are_covered(image: QImage) -> None:
    """Assert that every visible edge pixel has rendered source coverage."""
    width = image.width()
    height = image.height()
    for x in range(width):
        assert image.pixelColor(x, 0).alpha() == 255
        assert image.pixelColor(x, height - 1).alpha() == 255
    for y in range(height):
        assert image.pixelColor(0, y).alpha() == 255
        assert image.pixelColor(width - 1, y).alpha() == 255


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


def test_tiled_base_scroll_strip_repair_uses_layered_tile_path(
    qpane_with_image,
    monkeypatch,
) -> None:
    """Tiled base layers must not use direct-source strip repair."""
    qpane = qpane_with_image
    renderer = qpane.view().renderer
    tile_image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
    tile_image.fill(Qt.white)
    plan = make_render_plan(
        qpane.rect(),
        strategy=RenderStrategy.TILE,
        tiles_to_draw=(TileRenderData(tile_image, QPointF(0.0, 0.0)),),
        visible_tile_range=(0, 0, 0, 0),
    )
    calls = []

    def fail_direct_repair(*_args, **_kwargs):
        raise AssertionError("tiled strip repair must not use direct source drawing")

    monkeypatch.setattr(
        renderer, "_repair_base_raster_strips_directly", fail_direct_repair
    )
    monkeypatch.setattr(
        renderer,
        "_repair_layered_strips",
        lambda rects, repair_plan: calls.append((rects, repair_plan)) or True,
    )

    assert renderer._can_repair_base_strips_directly(plan) is False
    assert renderer._repair_base_buffer_strips([qpane.rect()], plan) is True
    assert calls == [([qpane.rect()], plan)]


def test_default_scene_scroll_repair_matches_full_redraw(qapp) -> None:
    """Default scene pan repair should match a clean full redraw."""
    qpane = _make_qpane_with_checker_image(qapp)
    try:
        start_pan = QPointF(0.0, 0.0)
        target_pan = QPointF(7.0, 5.0)
        expected = _render_clean_frame(qpane, target_pan)
        actual = _render_scrolled_frame(
            qpane,
            start_pan=start_pan,
            target_pan=target_pan,
        )
        assert_images_match(actual, expected)
    finally:
        qpane.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0, 2.5, 3.0])
@pytest.mark.parametrize(
    "target_pan",
    [
        QPointF(0.5, 0.0),
        QPointF(-0.75, 0.0),
        QPointF(0.0, 0.5),
        QPointF(0.0, -0.75),
        QPointF(0.5, 0.5),
    ],
)
def test_fractional_scroll_reuse_covers_viewport_edges(
    qapp,
    dpr: float,
    target_pan: QPointF,
) -> None:
    """Fractional scroll reuse should not expose uncovered edge strips."""
    qpane = _make_qpane_with_checker_image(qapp, size=1024, dpr=dpr)
    try:
        actual = _render_scrolled_frame(
            qpane,
            start_pan=QPointF(0.0, 0.0),
            target_pan=target_pan,
        )
        _assert_edges_are_covered(actual)
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_live_pan_uses_scroll_buffer_reuse(qapp) -> None:
    """Pan-only viewport changes should use renderer-owned scroll reuse."""
    qpane = _make_qpane_with_checker_image(qapp)
    try:
        presenter = qpane.view().presenter
        presenter.paint(
            is_blank=False,
            content_overlays={},
            scene_overlays={},
            overlays_suspended=False,
            draw_tool_overlay=None,
        )
        renderer = qpane.view().renderer
        before = renderer.snapshot_metrics()
        qpane.setPan(QPointF(6.0, 0.0))
        after = renderer.snapshot_metrics()
        assert after.scroll_attempts == before.scroll_attempts + 1
        assert after.scroll_hits == before.scroll_hits + 1
        assert after.full_redraws == before.full_redraws
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_live_pan_falls_back_to_full_dirty_when_scroll_repair_fails(
    qapp,
    monkeypatch,
) -> None:
    """Failed strip repair should schedule a full redraw instead of leaving stale pixels."""
    qpane = _make_qpane_with_checker_image(qapp)
    try:
        presenter = qpane.view().presenter
        presenter.paint(
            is_blank=False,
            content_overlays={},
            scene_overlays={},
            overlays_suspended=False,
            draw_tool_overlay=None,
        )
        renderer = qpane.view().renderer
        before = renderer.snapshot_metrics()
        monkeypatch.setattr(
            renderer,
            "_repair_base_buffer_strips",
            lambda _rects, _plan: False,
        )
        qpane.setPan(QPointF(6.0, 0.0))
        after = renderer.snapshot_metrics()
        assert after.scroll_attempts == before.scroll_attempts + 1
        assert after.scroll_misses == before.scroll_misses + 1
        assert not renderer._dirty_region.isEmpty()
        assert renderer._dirty_region.boundingRect().contains(qpane.rect())
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_scroll_repair_failure_restores_original_buffer(qapp, monkeypatch) -> None:
    """A failed repair must roll back the already-scrolled backing buffer."""
    qpane = _make_qpane_with_checker_image(qapp)
    try:
        view = qpane.view()
        renderer = view.renderer
        view.allocate_buffers()
        start_pan = QPointF(0.0, 0.0)
        view.viewport.pan = QPointF(start_pan)
        renderer.markDirty()
        start_plan = view.calculateRenderPlan(use_pan=start_pan, is_blank=False)
        assert start_plan is not None
        renderer.paint(start_plan)
        original_buffer = QImage(renderer.get_base_buffer())
        original_buffer_pan = QPointF(renderer._buffer_pan)
        monkeypatch.setattr(
            renderer,
            "_repair_base_buffer_strips",
            lambda _rects, _plan: False,
        )
        assert renderer.tryScrollBuffers(QPointF(9.0, 0.0)) is False
        assert renderer._buffer_pan == original_buffer_pan
        restored_buffer = renderer.get_base_buffer()
        assert restored_buffer is not None
        assert_images_match(QImage(restored_buffer), original_buffer)
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_public_scene_scroll_repair_matches_full_redraw(qapp) -> None:
    """Multi-layer public scene pan repair should match a clean full redraw."""
    qpane = QPane(features=())
    try:
        qpane.resize(96, 96)
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        first = checker_image(QRect(0, 0, 128, 128).size())
        second = checker_image(QRect(0, 0, 128, 128).size())
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [first, second],
                [None, None],
                [first_id, second_id],
            ),
            first_id,
        )
        request = QPaneSceneRequest(
            composition_id=None,
            title="Scroll repair scene",
            bounds=QRectF(0.0, 0.0, 256.0, 128.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=first_id,
                    placement=QRectF(0.0, 0.0, 128.0, 128.0),
                ),
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(128.0, 0.0, 128.0, 128.0),
                ),
            ),
        )
        qpane.composeScene(request)
        qpane.setZoom1To1()
        start_pan = QPointF(0.0, 0.0)
        target_pan = QPointF(9.0, 4.0)
        expected = _render_clean_frame(qpane, target_pan)
        actual = _render_scrolled_frame(
            qpane,
            start_pan=start_pan,
            target_pan=target_pan,
        )
        assert_images_match(actual, expected)
    finally:
        qpane.deleteLater()
        qapp.processEvents()


def test_clipped_public_scene_scroll_repair_matches_full_redraw(qapp) -> None:
    """Clipped scene layers should repair pan strips to the same pixels as redraw."""
    qpane = QPane(features=())
    try:
        qpane.resize(96, 96)
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        first = checker_image(QRect(0, 0, 128, 128).size())
        second = checker_image(QRect(0, 0, 128, 128).size())
        second.invertPixels()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [first, second],
                [None, None],
                [first_id, second_id],
            ),
            first_id,
        )
        request = QPaneSceneRequest(
            composition_id=None,
            title="Clipped scroll repair scene",
            bounds=QRectF(0.0, 0.0, 128.0, 128.0),
            layers=(
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=first_id,
                    placement=QRectF(0.0, 0.0, 128.0, 128.0),
                ),
                QPaneCatalogImageLayerRequest(
                    layer_id=uuid.uuid4(),
                    image_id=second_id,
                    placement=QRectF(0.0, 0.0, 128.0, 128.0),
                    clip=QPaneSceneClip(
                        "scene",
                        QRectF(48.0, 0.0, 80.0, 128.0),
                    ),
                ),
            ),
        )
        qpane.composeScene(request)
        qpane.setZoom1To1()
        start_pan = QPointF(0.0, 0.0)
        target_pan = QPointF(8.0, 6.0)
        expected = _render_clean_frame(qpane, target_pan)
        actual = _render_scrolled_frame(
            qpane,
            start_pan=start_pan,
            target_pan=target_pan,
        )
        assert_images_match(actual, expected)
    finally:
        qpane.deleteLater()
        qapp.processEvents()


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
