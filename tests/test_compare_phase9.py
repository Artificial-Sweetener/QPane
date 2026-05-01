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

"""Phase 9 tests for comparison rendering through scene layers."""

from __future__ import annotations

from pathlib import Path
import uuid

from PySide6.QtGui import QColor, QImage, Qt

from qpane import ComparisonOrientation, QPane
from qpane.scene.identity import SceneLayerTileKey, default_catalog_asset_key
from qpane.scene.model import ClipCoordinateSpace, LayerKind
from qpane.scene.render_plan import RasterLayerRenderItem, RenderStrategy
from qpane.scene.sources import CatalogImageSource


def _solid_image(
    width: int = 100,
    height: int = 100,
    color: Qt.GlobalColor | QColor = Qt.white,
) -> QImage:
    """Return a solid premultiplied test image."""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def _cleanup_qpane(qpane: QPane, qapp) -> None:
    """Release a test widget through Qt's event loop."""
    qpane.deleteLater()
    qapp.processEvents()


class RecordingTileManager:
    """Tile manager test double that records every requested tile key."""

    def __init__(self, *, tile_size: int = 256, tile_overlap: int = 0) -> None:
        """Create a deterministic tile manager replacement."""
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap
        self.requested = []
        self.cancelled = None

    def calculate_grid_dimensions(self, width: int, height: int) -> tuple[int, int]:
        """Return the tile grid required for the source image dimensions."""
        cols = max(1, (width + self.tile_size - 1) // self.tile_size)
        rows = max(1, (height + self.tile_size - 1) // self.tile_size)
        return cols, rows

    def get_tile(self, identifier, _source_image) -> QImage:
        """Record the request and return a paintable tile payload."""
        self.requested.append(identifier)
        return _solid_image(self.tile_size, self.tile_size, Qt.black)

    def cancel_invisible_workers(self, visible_ids) -> None:
        """Record cancellation visibility for base-layer tile planning."""
        self.cancelled = frozenset(visible_ids)


def _tile_columns_for_source(tile_manager: RecordingTileManager, image_id: uuid.UUID):
    """Return requested tile columns for one source image ID."""
    return [
        key.col for key in tile_manager.requested if key.asset_key.source_id == image_id
    ]


def _tile_rows_for_source(tile_manager: RecordingTileManager, image_id: uuid.UUID):
    """Return requested tile rows for one source image ID."""
    return [
        key.row for key in tile_manager.requested if key.asset_key.source_id == image_id
    ]


def _tile_key_for_item(
    item: RasterLayerRenderItem,
    *,
    row: int,
    col: int,
    pyramid_scale: float | None = None,
) -> SceneLayerTileKey:
    """Return a tile key targeting ``item`` for dirty-region tests."""
    return SceneLayerTileKey(
        asset_key=item.asset_key,
        pyramid_asset_key=item.pyramid_asset_key,
        pyramid_scale=(item.pyramid_scale if pyramid_scale is None else pyramid_scale),
        row=row,
        col=col,
    )


def test_compare_catalog_image_resolves_as_second_scene_raster(qapp) -> None:
    """Catalog comparison should contribute a clipped image layer."""
    qpane = QPane(features=())
    qpane.resize(100, 100)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        base_image = _solid_image(color=Qt.red)
        compare_image = _solid_image(color=Qt.blue)
        image_map = QPane.imageMapFromLists(
            [base_image, compare_image],
            [Path("base.png"), Path("compare.png")],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.25, ComparisonOrientation.VERTICAL)

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        raster_items = [
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        ]
        assert len(raster_items) == 2
        base_item, compare_item = raster_items
        assert base_item.descriptor.kind == LayerKind.IMAGE
        assert compare_item.descriptor.kind == LayerKind.IMAGE
        assert isinstance(base_item.descriptor.source, CatalogImageSource)
        assert isinstance(compare_item.descriptor.source, CatalogImageSource)
        assert base_item.descriptor.source.image_id == base_id
        assert compare_item.descriptor.source.image_id == compare_id
        assert compare_item.asset_key.scene_id == plan.scene_id
        assert compare_item.asset_key.source_id == compare_id
        assert compare_item.asset_key.layer_id == compare_item.descriptor.layer_id
        assert compare_item.pyramid_asset_key == default_catalog_asset_key(
            compare_id,
            revision=1,
            source_path=Path("compare.png"),
        )
        assert compare_item.asset_key != compare_item.pyramid_asset_key
        assert compare_item.clip is not None
        assert (
            compare_item.clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_SCENE
        )
        assert compare_item.clip.x == 0.25
        assert compare_item.clip.width == 0.75
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tile_ready_dirty_marking_does_not_build_render_plan(qapp, monkeypatch) -> None:
    """Tile-ready callbacks should use geometry instead of full paint planning."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(1024, 1024, Qt.red),
                _solid_image(1024, 1024, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        qpane.view().presenter.tile_manager = RecordingTileManager(
            tile_size=256,
            tile_overlap=0,
        )

        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        qapp.processEvents()
        base_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
            and item.asset_key.source_id == base_id
        )
        key = _tile_key_for_item(base_item, row=1, col=1)
        dirty_rects = []
        monkeypatch.setattr(
            qpane.view().presenter,
            "calculateRenderPlan",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("tile-ready must not build a render plan")
            ),
        )
        monkeypatch.setattr(
            qpane.view().swap_delegate, "_mark_dirty", dirty_rects.append
        )

        qpane.view().handle_tile_ready(key)

        assert dirty_rects
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tile_ready_dirty_marking_rejects_stale_layer_asset(qapp) -> None:
    """Tile-ready geometry should ignore keys outside the active scene."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red)],
                [None],
                [base_id],
            ),
            base_id,
        )
        qpane.setZoom1To1()
        stale_asset = default_catalog_asset_key(
            uuid.uuid4(),
            revision=0,
            source_path=None,
        )
        key = SceneLayerTileKey(
            asset_key=stale_asset,
            pyramid_asset_key=stale_asset,
            pyramid_scale=1.0,
            row=1,
            col=1,
        )

        assert qpane.view().presenter.dirty_rect_for_tile_key(key) is None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tile_ready_dirty_marking_rejects_stale_pyramid_scale(qapp) -> None:
    """Tile-ready geometry should ignore keys from an old pyramid level."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red)],
                [None],
                [base_id],
            ),
            base_id,
        )
        qpane.setZoom1To1()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        base_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        )
        key = _tile_key_for_item(
            base_item,
            row=1,
            col=1,
            pyramid_scale=base_item.pyramid_scale * 2.0,
        )

        assert qpane.view().presenter.dirty_rect_for_tile_key(key) is None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tile_ready_dirty_marking_maps_base_raster_layer(qapp) -> None:
    """Tile-ready geometry should mark dirty rects for active base tiles."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red)],
                [None],
                [base_id],
            ),
            base_id,
        )
        qpane.setZoom1To1()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        base_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
        )
        qpane.view().presenter.tile_manager = RecordingTileManager(
            tile_size=256,
            tile_overlap=0,
        )
        key = _tile_key_for_item(base_item, row=1, col=1)

        dirty_rect = qpane.view().presenter.dirty_rect_for_tile_key(key)

        assert dirty_rect is not None
        assert not dirty_rect.isEmpty()
    finally:
        _cleanup_qpane(qpane, qapp)


def test_tile_ready_dirty_marking_maps_comparison_layer(qapp) -> None:
    """Tile-ready geometry should mark dirty rects for active non-base tiles."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red), _solid_image(1024, 1024, Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        compare_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
            and item.asset_key.source_id == compare_id
        )
        qpane.view().presenter.tile_manager = RecordingTileManager(
            tile_size=256,
            tile_overlap=0,
        )
        key = _tile_key_for_item(compare_item, row=1, col=2)

        dirty_rect = qpane.view().presenter.dirty_rect_for_tile_key(key)

        assert dirty_rect is not None
        assert not dirty_rect.isEmpty()
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_split_prevents_dirty_marking_outside_revealed_side(qapp) -> None:
    """Compare tile-ready geometry should ignore clipped-away tiles."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red), _solid_image(1024, 1024, Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        compare_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
            and item.asset_key.source_id == compare_id
        )
        qpane.view().presenter.tile_manager = RecordingTileManager(
            tile_size=256,
            tile_overlap=0,
        )
        key = _tile_key_for_item(compare_item, row=1, col=0)

        assert qpane.view().presenter.dirty_rect_for_tile_key(key) is None
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_split_clips_partially_intersecting_dirty_rect(qapp) -> None:
    """Compare tile-ready geometry should dirty only the visible tile fragment."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(1024, 1024, Qt.red), _solid_image(1024, 1024, Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        compare_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
            and item.asset_key.source_id == compare_id
        )
        qpane.view().presenter.tile_manager = RecordingTileManager(
            tile_size=256,
            tile_overlap=0,
        )
        key = _tile_key_for_item(compare_item, row=1, col=2)

        dirty_rect = qpane.view().presenter.dirty_rect_for_tile_key(key)

        assert dirty_rect is not None
        assert 0 < dirty_rect.height() < 258
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_vertical_split_limits_requested_tiles_to_revealed_side(qapp) -> None:
    """Vertical comparison clips should constrain compare tile requests."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(1024, 1024, Qt.red),
                _solid_image(1024, 1024, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        tile_manager = RecordingTileManager(tile_size=256, tile_overlap=0)
        qpane.view().presenter.tile_manager = tile_manager

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        compare_item = next(
            item
            for item in plan.render_items
            if isinstance(item, RasterLayerRenderItem)
            and item.asset_key.source_id == compare_id
        )
        assert compare_item.strategy == RenderStrategy.TILE
        assert compare_item.clip is not None
        compare_columns = _tile_columns_for_source(tile_manager, compare_id)
        base_columns = _tile_columns_for_source(tile_manager, base_id)
        assert compare_columns
        assert min(compare_columns) >= 1
        assert min(base_columns) == 0

    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_tile_cancellation_keeps_base_and_compare_visible_keys(qapp) -> None:
    """Tile cancellation should receive visible keys from every raster layer."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(2048, 2048, Qt.red),
                _solid_image(2048, 2048, Qt.blue),
            ],
            [Path("base-cancel.png"), Path("compare-cancel.png")],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        tile_manager = RecordingTileManager(tile_size=256, tile_overlap=0)
        qpane.view().presenter.tile_manager = tile_manager

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert tile_manager.requested
        assert tile_manager.cancelled == frozenset(tile_manager.requested)
        assert any(key.asset_key.source_id == base_id for key in tile_manager.cancelled)
        assert any(
            key.asset_key.source_id == compare_id for key in tile_manager.cancelled
        )
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_horizontal_split_limits_requested_tiles_to_revealed_side(qapp) -> None:
    """Horizontal comparison clips should constrain compare tile requests."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(1024, 1024, Qt.red),
                _solid_image(1024, 1024, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.HORIZONTAL)
        qpane.setZoom1To1()
        tile_manager = RecordingTileManager(tile_size=256, tile_overlap=0)
        qpane.view().presenter.tile_manager = tile_manager

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        compare_rows = _tile_rows_for_source(tile_manager, compare_id)
        base_rows = _tile_rows_for_source(tile_manager, base_id)
        assert compare_rows
        assert min(compare_rows) >= 1
        assert min(base_rows) == 0
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_empty_split_requests_no_compare_tiles(qapp) -> None:
    """A fully hidden comparison side should not request comparison tiles."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(1024, 1024, Qt.red),
                _solid_image(1024, 1024, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(1.0, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        tile_manager = RecordingTileManager(tile_size=256, tile_overlap=0)
        qpane.view().presenter.tile_manager = tile_manager

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert _tile_columns_for_source(tile_manager, base_id)
        assert _tile_columns_for_source(tile_manager, compare_id) == []
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_full_split_keeps_normal_compare_tile_span(qapp) -> None:
    """A fully revealed comparison side should keep normal viewport tile culling."""
    qpane = QPane(features=())
    qpane.resize(256, 256)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(1024, 1024, Qt.red),
                _solid_image(1024, 1024, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.0, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        tile_manager = RecordingTileManager(tile_size=256, tile_overlap=0)
        qpane.view().presenter.tile_manager = tile_manager

        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert _tile_columns_for_source(tile_manager, compare_id) == (
            _tile_columns_for_source(tile_manager, base_id)
        )
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_split_clips_rendered_pixels(qapp) -> None:
    """Renderer should apply the comparison layer clip during painting."""
    qpane = QPane(features=())
    qpane.resize(100, 100)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [_solid_image(color=Qt.red), _solid_image(color=Qt.blue)],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        qpane.view().allocate_buffers()
        qpane.view().mark_dirty()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None

        qpane.view().renderer.paint(plan)
        buffer = qpane.view().renderer.get_base_buffer()

        assert buffer is not None
        assert buffer.pixelColor(25, 50) == QColor(Qt.red)
        assert buffer.pixelColor(75, 50) == QColor(Qt.blue)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_catalog_image_uses_scene_placement(qapp) -> None:
    """Catalog comparisons should fill their scene placement even when sizes differ."""
    qpane = QPane(features=())
    qpane.resize(100, 100)
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [
                _solid_image(100, 100, Qt.red),
                _solid_image(50, 100, Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        )
        qpane.setImagesByID(image_map, base_id)
        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
        qpane.setZoom1To1()
        qpane.view().allocate_buffers()
        qpane.view().mark_dirty()
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None

        qpane.view().renderer.paint(plan)
        buffer = qpane.view().renderer.get_base_buffer()

        assert buffer is not None
        assert buffer.pixelColor(25, 50) == QColor(Qt.red)
        assert buffer.pixelColor(75, 50) == QColor(Qt.blue)
        assert buffer.pixelColor(90, 50) == QColor(Qt.blue)
    finally:
        _cleanup_qpane(qpane, qapp)


def test_comparison_changes_do_not_emit_catalog_mutations(qapp) -> None:
    """Comparison-only updates should use comparisonChanged, not catalogChanged."""
    qpane = QPane(features=())
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(), _solid_image(color=Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        catalog_reasons: list[str] = []
        comparison_states = []
        qpane.catalogChanged.connect(lambda event: catalog_reasons.append(event.reason))
        qpane.comparisonChanged.connect(comparison_states.append)

        qpane.setComparisonImageID(compare_id)
        qpane.setComparisonSplit(0.25, ComparisonOrientation.VERTICAL)
        qpane.clearComparisonImage()

        assert catalog_reasons == []
        assert len(comparison_states) == 3
        assert comparison_states[-1].enabled is False
    finally:
        _cleanup_qpane(qpane, qapp)


def test_catalog_comparison_survives_source_image_replacement(qapp) -> None:
    """Replacing the catalog comparison source should keep comparison enabled."""
    qpane = QPane(features=())
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(color=Qt.red), _solid_image(color=Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        qpane.setComparisonImageID(compare_id)

        qpane.addImage(compare_id, _solid_image(color=Qt.green), None)
        plan = qpane.view().calculateRenderPlan(is_blank=False)

        assert qpane.comparisonState().enabled is True
        assert qpane.comparisonState().source_id == compare_id
        assert plan is not None
        compare_item = plan.render_items[1]
        assert isinstance(compare_item, RasterLayerRenderItem)
        assert compare_item.asset_key.source_id == compare_id
    finally:
        _cleanup_qpane(qpane, qapp)


def test_catalog_comparison_clears_when_source_image_is_removed(qapp) -> None:
    """Removing the catalog comparison source should disable comparison."""
    qpane = QPane(features=())
    try:
        base_id = uuid.uuid4()
        compare_id = uuid.uuid4()
        qpane.setImagesByID(
            QPane.imageMapFromLists(
                [_solid_image(color=Qt.red), _solid_image(color=Qt.blue)],
                [None, None],
                [base_id, compare_id],
            ),
            base_id,
        )
        qpane.setComparisonImageID(compare_id)

        qpane.removeImageByID(compare_id)

        assert qpane.comparisonState().enabled is False
        plan = qpane.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        assert (
            len(
                [
                    item
                    for item in plan.render_items
                    if isinstance(item, RasterLayerRenderItem)
                ]
            )
            == 1
        )
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_horizontal_split_updates_state(qapp) -> None:
    """Horizontal split updates should clamp position and expose state."""
    qpane = QPane(features=())
    try:
        qpane.setComparisonSplit(2.0, "horizontal")

        state = qpane.comparisonState()

        assert state.split_position == 1.0
        assert state.orientation == ComparisonOrientation.HORIZONTAL
    finally:
        _cleanup_qpane(qpane, qapp)


def test_compare_rejects_unknown_catalog_image(qapp) -> None:
    """Catalog comparison sources must already exist."""
    qpane = QPane(features=())
    try:
        try:
            qpane.setComparisonImageID(uuid.uuid4())
        except KeyError:
            raised = True
        else:
            raised = False

        assert raised is True
    finally:
        _cleanup_qpane(qpane, qapp)
