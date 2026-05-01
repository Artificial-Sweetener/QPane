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

"""Phase 3 tests for private scene render-plan snapshots."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QTransform

from qpane.scene.default_scene import build_default_catalog_scene
from qpane.scene.identity import SceneLayerAssetKey
from qpane.scene.render_plan import (
    RasterLayerRenderItem,
    RenderStrategy,
    SceneContentSnapshot,
    SceneHitTestItem,
    SceneRenderPlan,
    TileRenderData,
)


def test_scene_render_plan_keeps_ordered_items_and_base_item() -> None:
    """Scene render plans should expose ordered immutable raster snapshots."""
    image_id = uuid.uuid4()
    path = Path("phase3.png")
    scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(32, 24),
        source_path=path,
        revision=2,
    )
    layer = scene.layers[0]
    source_image = QImage(16, 12, QImage.Format_ARGB32_Premultiplied)
    asset_key = SceneLayerAssetKey(
        scene_id=scene.scene_id,
        layer_id=layer.layer_id,
        source_id=image_id,
        source_kind="catalog-image",
        source_revision=2,
        source_path=path,
    )
    raster_item = RasterLayerRenderItem(
        descriptor=layer,
        source_image=source_image,
        asset_key=asset_key,
        pyramid_asset_key=asset_key,
        pyramid_scale=0.5,
        transform=QTransform(),
        placement=layer.placement,
        clip=layer.clip,
        strategy=RenderStrategy.DIRECT,
        render_hint_enabled=True,
        debug_draw_tile_grid=False,
        tiles_to_draw=(),
        tile_size=0,
        tile_overlap=0,
        max_tile_cols=0,
        max_tile_rows=0,
        visible_tile_range=None,
    )
    hit_item = SceneHitTestItem(
        scene_id=scene.scene_id,
        layer_id=layer.layer_id,
        bounds=layer.placement,
        enabled=True,
        selectable=False,
        role="base-image",
    )
    plan = SceneRenderPlan(
        scene_id=scene.scene_id,
        scene_bounds=scene.bounds,
        content_bounds=scene.bounds,
        content_snapshot=SceneContentSnapshot(
            scene_id=scene.scene_id,
            base_asset_key=asset_key,
            base_image_size=QSize(32, 24),
            scene_bounds=scene.bounds,
            active_content_bounds=scene.bounds,
            current_path=path,
        ),
        zoom=1.0,
        current_pan=QPointF(0.0, 0.0),
        qpane_rect=QRect(0, 0, 32, 24),
        physical_viewport_rect=QRectF(0.0, 0.0, 32.0, 24.0),
        render_items=(raster_item,),
        hit_test_items=(hit_item,),
    )

    assert plan.base_raster_item is raster_item
    assert plan.render_items == (raster_item,)
    assert plan.hit_test_items == (hit_item,)
    with pytest.raises(FrozenInstanceError):
        plan.render_items = ()


def test_raster_layer_render_item_validates_scale_and_tile_metadata() -> None:
    """Raster layer items should reject invalid render-planning metadata."""
    image_id = uuid.uuid4()
    scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(32, 24),
        source_path=None,
        revision=0,
    )
    layer = scene.layers[0]
    asset_key = SceneLayerAssetKey(
        scene_id=scene.scene_id,
        layer_id=layer.layer_id,
        source_id=image_id,
        source_kind="catalog-image",
        source_revision=0,
    )

    with pytest.raises(ValueError, match="pyramid scale"):
        RasterLayerRenderItem(
            descriptor=layer,
            source_image=QImage(16, 12, QImage.Format_ARGB32_Premultiplied),
            asset_key=asset_key,
            pyramid_asset_key=asset_key,
            pyramid_scale=0.0,
            transform=QTransform(),
            placement=layer.placement,
            clip=layer.clip,
            strategy=RenderStrategy.DIRECT,
            render_hint_enabled=True,
            debug_draw_tile_grid=False,
            tiles_to_draw=(),
            tile_size=0,
            tile_overlap=0,
            max_tile_cols=0,
            max_tile_rows=0,
            visible_tile_range=None,
        )


def test_render_plan_detaches_mutable_geometry_without_copying_images(
    monkeypatch,
) -> None:
    """Render-plan snapshots should share image inputs but detach geometry."""
    image_id = uuid.uuid4()
    scene = build_default_catalog_scene(
        image_id=image_id,
        image_size=QSize(32, 24),
        source_path=None,
        revision=0,
    )
    layer = scene.layers[0]
    asset_key = SceneLayerAssetKey(
        scene_id=scene.scene_id,
        layer_id=layer.layer_id,
        source_id=image_id,
        source_kind="catalog-image",
        source_revision=0,
    )
    source_image = QImage(16, 12, QImage.Format_ARGB32_Premultiplied)
    source_image.fill(QColor("red"))
    tile_image = QImage(4, 4, QImage.Format_ARGB32_Premultiplied)
    tile_image.fill(QColor("black"))

    def fail_copy(*_args, **_kwargs):
        raise AssertionError("render-plan construction must not copy image payloads")

    monkeypatch.setattr(source_image, "copy", fail_copy)
    monkeypatch.setattr(tile_image, "copy", fail_copy)

    draw_pos = QPointF(2.0, 3.0)
    transform = QTransform()
    transform.translate(4.0, 5.0)
    tile = TileRenderData(tile_image, draw_pos)
    raster_item = RasterLayerRenderItem(
        descriptor=layer,
        source_image=source_image,
        asset_key=asset_key,
        pyramid_asset_key=asset_key,
        pyramid_scale=0.5,
        transform=transform,
        placement=layer.placement,
        clip=layer.clip,
        strategy=RenderStrategy.TILE,
        render_hint_enabled=True,
        debug_draw_tile_grid=False,
        tiles_to_draw=[tile],
        tile_size=4,
        tile_overlap=1,
        max_tile_cols=2,
        max_tile_rows=3,
        visible_tile_range=(0, 1, 0, 1),
    )
    current_pan = QPointF(8.0, 9.0)
    qpane_rect = QRect(0, 0, 32, 24)
    viewport_rect = QRectF(0.0, 0.0, 32.0, 24.0)
    plan = SceneRenderPlan(
        scene_id=scene.scene_id,
        scene_bounds=scene.bounds,
        content_bounds=scene.bounds,
        content_snapshot=SceneContentSnapshot(
            scene_id=scene.scene_id,
            base_asset_key=asset_key,
            base_image_size=QSize(32, 24),
            scene_bounds=scene.bounds,
            active_content_bounds=scene.bounds,
            current_path=None,
        ),
        zoom=1.0,
        current_pan=current_pan,
        qpane_rect=qpane_rect,
        physical_viewport_rect=viewport_rect,
        render_items=[raster_item],
        hit_test_items=[],
    )

    draw_pos.setX(20.0)
    transform.translate(20.0, 30.0)
    current_pan.setX(80.0)
    qpane_rect.setWidth(320)
    viewport_rect.setHeight(240.0)

    assert raster_item.source_image is source_image
    assert tile.image is tile_image
    assert raster_item.source_image.pixelColor(0, 0) == QColor("red")
    assert tile.image.pixelColor(0, 0) == QColor("black")
    assert tile.draw_pos == QPointF(2.0, 3.0)
    assert raster_item.transform.m31() == 4.0
    assert raster_item.transform.m32() == 5.0
    assert raster_item.tiles_to_draw == (tile,)
    assert plan.current_pan == QPointF(8.0, 9.0)
    assert plan.qpane_rect == QRect(0, 0, 32, 24)
    assert plan.physical_viewport_rect == QRectF(0.0, 0.0, 32.0, 24.0)
    assert plan.render_items == (raster_item,)
    assert plan.hit_test_items == ()
