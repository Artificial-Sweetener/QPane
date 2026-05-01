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

import math
from pathlib import Path
import uuid

from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QImage, QPixmap, Qt
from PySide6.QtWidgets import QWidget

from qpane.core import CacheSettings
from qpane.rendering import (
    RenderingPresenter,
    ViewportZoomMode,
)
from qpane.scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    base_image_layer_id,
    default_catalog_asset_key,
    default_scene_id,
)
from qpane.scene.model import LayerKind
from qpane.scene.mask_adapter import MaskServiceSceneProvider
from qpane.scene.registry import (
    CatalogLayerSourceResolver,
    LayerSourceResolverRegistry,
    SceneProviderRegistry,
)
from qpane.scene.render_plan import RenderStrategy
from qpane.scene.sources import MaskLayerSource


def _cleanup_qpane(widget: QWidget, qapp) -> None:
    widget.deleteLater()
    qapp.processEvents()


def _make_image(
    width: int,
    height: int,
    color: Qt.GlobalColor = Qt.white,
) -> QImage:
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


class StubSettings:
    """Lightweight Config replacement covering presenter dependencies."""

    def __init__(
        self,
        *,
        tile_size: int = 256,
        tile_overlap: int = 0,
        draw_tile_grid: bool = False,
        min_view_size_px: int = 4,
        canvas_expansion_factor: float = 1.0,
        safe_min_zoom: float = 1e-3,
    ) -> None:
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap
        self.draw_tile_grid = draw_tile_grid
        self.min_view_size_px = min_view_size_px
        self.canvas_expansion_factor = canvas_expansion_factor
        self.safe_min_zoom = safe_min_zoom
        self.cache = CacheSettings(mode="hard", budget_mb=8)
        self.cache.set_override_mb("tiles", 8)


class StubView:
    """Expose the viewport reference expected by CoordinateContext."""

    def __init__(self) -> None:
        self.viewport = None


class StubQPane(QWidget):
    """Minimal QWidget exposing the attributes accessed by RenderingPresenter."""

    def __init__(
        self,
        *,
        settings: StubSettings,
        size: tuple[int, int],
        dpr: float,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.settings = settings
        self._dpr = dpr
        self._view = StubView()
        self.original_image = QImage()
        self.currentImagePath: Path | None = Path("stub.png")
        self.mask_service = None
        self._scene_providers = SceneProviderRegistry()
        self._source_resolvers = LayerSourceResolverRegistry()
        self._current_image_id = uuid.uuid4()
        self._is_blank = True
        self.resize(*size)

    def view(self):
        """Return the stub view the presenter expects."""
        return self._view

    def attach_presenter(self, presenter) -> None:
        """Backfill the viewport reference once the presenter is built."""
        self._view.presenter = presenter
        self._view.viewport = presenter.viewport

    def devicePixelRatioF(self) -> float:  # pragma: no cover - Qt override
        return self._dpr

    def set_device_pixel_ratio(self, dpr: float) -> None:
        """Update the DPR backing physical viewport calculations."""
        self._dpr = dpr

    def physicalViewportRect(self) -> QRectF:  # pragma: no cover - Qt override
        rect = QRectF(self.rect())
        rect.setWidth(rect.width() * self._dpr)
        rect.setHeight(rect.height() * self._dpr)
        return rect

    def currentImageID(self):  # pragma: no cover - stub for presenter lookup
        return self._current_image_id

    def sceneProviderRegistry(self) -> SceneProviderRegistry:
        """Return the private scene-provider registry used by the presenter."""
        return self._scene_providers

    def layerSourceResolverRegistry(self) -> LayerSourceResolverRegistry:
        """Return the private source resolver registry used by the presenter."""
        return self._source_resolvers


class StubCatalog:
    """Simplified catalog returning a preloaded QImage."""

    def __init__(
        self, image: QImage, *, image_id: uuid.UUID, path: Path | None
    ) -> None:
        self._base_image = image
        self._current_image_id = image_id
        self._current_path = path
        self._resolver = None
        self.revision = 0
        self.best_fit_calls: list[tuple[SceneLayerAssetKey | None, float]] = []

    @property
    def base_image(self) -> QImage:
        """Return the image used when best-fit logic is not overridden."""
        return self._base_image

    def set_base_image(self, image: QImage) -> None:
        """Replace the stored image backing presenter lookups."""
        self._base_image = image

    def set_current(
        self,
        *,
        image: QImage,
        image_id: uuid.UUID,
        path: Path | None,
    ) -> None:
        """Replace the current catalog content."""
        self._base_image = image
        self._current_image_id = image_id
        self._current_path = path

    def set_path(self, path: Path | None) -> None:
        """Replace the current source path."""
        self._current_path = path

    def set_best_fit_resolver(self, resolver) -> None:
        """Inject a callable that mirrors ImageCatalog.getBestFitImageForAsset."""
        self._resolver = resolver

    def getBestFitImageForAsset(self, asset_key, width):  # pragma: no cover
        self.best_fit_calls.append((asset_key, width))
        if self._resolver is not None:
            return self._resolver(asset_key, width)
        return self._base_image

    def getRevision(self, image_id):  # pragma: no cover - simple passthrough
        return self.revision

    def defaultAssetKeyForImage(
        self, image_id
    ):  # pragma: no cover - simple passthrough
        return default_catalog_asset_key(
            image_id,
            revision=self.revision,
            source_path=self._current_path,
        )

    def getCurrentId(self):  # pragma: no cover - simple passthrough
        return self._current_image_id

    def getCurrentImage(self):  # pragma: no cover - simple passthrough
        return self._base_image

    def getCurrentPath(self):  # pragma: no cover - simple passthrough
        return self._current_path


class _NullHandle:
    """Provide the cancel API expected by TileManager."""

    def cancel(self) -> None:  # pragma: no cover - noop helper
        return None


class NoopExecutor:
    """Executor shim satisfying TileManager without spinning threads."""

    def submit(self, *_args, **_kwargs):  # pragma: no cover - noop helper
        return _NullHandle()


class PresenterHarness:
    """Bundle a stub qpane, catalog, and presenter for fast tests."""

    def __init__(
        self,
        *,
        qpane_size: tuple[int, int] = (256, 256),
        image_size: tuple[int, int] = (128, 128),
        color: Qt.GlobalColor = Qt.white,
        dpr: float = 1.0,
    ) -> None:
        self.settings = StubSettings()
        self.qpane = StubQPane(settings=self.settings, size=qpane_size, dpr=dpr)
        base_image = _make_image(image_size[0], image_size[1], color)
        self.catalog = StubCatalog(
            base_image,
            image_id=self.qpane._current_image_id,
            path=self.qpane.currentImagePath,
        )
        self.qpane.layerSourceResolverRegistry().register(
            CatalogLayerSourceResolver(self.catalog)
        )
        self.executor = NoopExecutor()
        self.qpane.original_image = base_image
        self.presenter = RenderingPresenter(
            qpane=self.qpane,
            catalog=self.catalog,
            cache_registry=None,
            executor=self.executor,
        )
        self.qpane.attach_presenter(self.presenter)
        self.viewport = self.presenter.viewport
        self.viewport.setContentSize(base_image.size())

    def set_image(
        self,
        image: QImage,
        *,
        path: Path | None = None,
        image_id: uuid.UUID | None = None,
    ) -> None:
        """Update the original image and catalog backing data."""
        next_id = image_id if image_id is not None else self.qpane._current_image_id
        next_path = path if path is not None else self.qpane.currentImagePath
        self.catalog.set_current(image=image, image_id=next_id, path=next_path)
        self.qpane.original_image = image
        if path is not None:
            self.qpane.currentImagePath = path
        if image_id is not None:
            self.qpane._current_image_id = image_id
        self.viewport.setContentSize(image.size())

    def set_catalog_resolver(self, resolver) -> None:
        """Proxy helper for custom best-fit lookups."""
        self.catalog.set_best_fit_resolver(resolver)

    def set_path(self, path: Path | None) -> None:
        """Update the current catalog path."""
        self.qpane.currentImagePath = path
        self.catalog.set_path(path)

    def resize_qpane(self, width: int, height: int) -> None:
        """Resize the qpane widget and trigger viewport updates."""
        self.qpane.resize(width, height)

    def set_device_pixel_ratio(self, dpr: float) -> None:
        """Update the qpane DPR used by CoordinateContext."""
        self.qpane.set_device_pixel_ratio(dpr)


class StubTileManager:
    def __init__(self, tile_size: int = 128, tile_overlap: int = 0) -> None:
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap
        self.requested: list = []
        self.cancelled = None

    def calculate_grid_dimensions(self, width: int, height: int) -> tuple[int, int]:
        cols = max(1, math.ceil(width / self.tile_size))
        rows = max(1, math.ceil(height / self.tile_size))
        return cols, rows

    def get_tile(self, identifier: SceneLayerTileKey, source_image) -> QImage:
        tile = _make_image(self.tile_size, self.tile_size, Qt.black)
        self.requested.append(identifier)
        return tile

    def cancel_invisible_workers(self, visible_ids) -> None:
        self.cancelled = frozenset(visible_ids)


class StubMaskLayer:
    """Small mask layer stand-in for presenter planning tests."""

    def __init__(self, image: QImage, *, opacity: float) -> None:
        self.mask_image = image
        self.opacity = opacity


class StubMaskManager:
    """Expose mask order and layer lookup for scene adapter tests."""

    def __init__(self, layers: dict[uuid.UUID, StubMaskLayer]) -> None:
        self._layers = layers

    def get_mask_ids_for_image(self, _image_id: uuid.UUID) -> list[uuid.UUID]:
        """Return masks in visual order."""
        return list(self._layers)

    def get_layer(self, mask_id: uuid.UUID) -> StubMaskLayer | None:
        """Return a configured mask layer."""
        return self._layers.get(mask_id)


class StubMaskController:
    """Expose stable render revisions for presenter tests."""

    def __init__(self, revisions: dict[uuid.UUID, int]) -> None:
        self._revisions = revisions

    def maskRenderRevision(self, mask_id: uuid.UUID) -> int:
        """Return the configured render revision for ``mask_id``."""
        return self._revisions[mask_id]


def _old_qpane_visible_tile_range(
    *,
    source_size: QSize,
    physical_viewport_rect: QRectF,
    zoom: float,
    pan: QPointF,
    pyramid_scale: float,
    tile_size: int,
    tile_overlap: int,
) -> tuple[int, int, int, int]:
    """Return the old direct viewport-to-source visible tile range."""
    effective_zoom = zoom / pyramid_scale
    viewport_center = QPointF(physical_viewport_rect.center())
    source_center = QPointF(source_size.width() / 2.0, source_size.height() / 2.0)
    top_left = (
        physical_viewport_rect.topLeft() - viewport_center - pan
    ) / effective_zoom + source_center
    bottom_right = (
        physical_viewport_rect.bottomRight() - viewport_center - pan
    ) / effective_zoom + source_center
    source_rect = (
        QRectF(top_left, bottom_right)
        .normalized()
        .intersected(
            QRectF(0.0, 0.0, float(source_size.width()), float(source_size.height()))
        )
    )
    if source_rect.isEmpty():
        return 0, -1, 0, -1
    stride = tile_size - tile_overlap
    max_cols = max(1, math.ceil(source_size.width() / tile_size))
    max_rows = max(1, math.ceil(source_size.height() / tile_size))
    start_col = max(0, int(source_rect.left() / stride) - 1)
    start_row = max(0, int(source_rect.top() / stride) - 1)
    end_col = min(max_cols - 1, int(source_rect.right() / stride) + 1)
    end_row = min(max_rows - 1, int(source_rect.bottom() / stride) + 1)
    if start_col > end_col or start_row > end_row:
        return 0, -1, 0, -1
    return start_row, end_row, start_col, end_col


class StubMaskService:
    """Provide mask manager/controller and colorized pixmaps."""

    def __init__(
        self,
        manager: StubMaskManager,
        controller: StubMaskController,
    ) -> None:
        self.manager = manager
        self.controller = controller
        self.calls: list[tuple[uuid.UUID, float | None]] = []

    def getColorizedMaskById(
        self, mask_id: uuid.UUID, *, scale: float | None = None
    ) -> QPixmap:
        """Return a pixmap representing the requested mask render."""
        self.calls.append((mask_id, scale))
        source = self.manager.get_layer(mask_id)
        assert source is not None
        size = source.mask_image.size()
        if scale is not None:
            size = size * scale
        image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.magenta)
        return QPixmap.fromImage(image)


def test_presenter_calculate_render_plan_blank_returns_none(qapp):
    harness = PresenterHarness(qpane_size=(64, 64), image_size=(32, 32))
    try:
        plan = harness.presenter.calculateRenderPlan(is_blank=True)
        assert plan is None
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_render_plan_uses_best_fit_source_and_transform(qapp):
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(400, 200),
        color=Qt.red,
    )
    try:
        source_image = _make_image(200, 100, Qt.green)
        image_id = uuid.uuid4()
        harness.set_image(
            harness.qpane.original_image,
            path=Path("best-fit.png"),
            image_id=image_id,
        )
        harness.viewport.zoom = 0.5
        harness.viewport.pan = QPointF(12.0, -8.0)

        expected_key = default_catalog_asset_key(
            image_id,
            revision=0,
            source_path=Path("best-fit.png"),
        )

        def best_fit_resolver(requested_key, target_width):
            assert requested_key == expected_key
            assert math.isclose(target_width, 200.0)
            return source_image

        harness.set_catalog_resolver(best_fit_resolver)
        plan = harness.presenter.calculateRenderPlan(is_blank=False)
        assert plan is not None
        item = plan.base_raster_item
        assert item is not None
        assert harness.catalog.best_fit_calls == [(expected_key, 200.0)]
        assert item.source_image.size() == source_image.size()
        assert item.source_image.pixelColor(0, 0) == source_image.pixelColor(0, 0)
        assert math.isclose(item.pyramid_scale, 0.5)
        assert item.strategy == RenderStrategy.DIRECT
        assert plan.current_pan == QPointF(12.0, -8.0)
        assert plan.physical_viewport_rect == QRectF(0, 0, 300, 200)
        mapped_center = item.transform.map(QPointF(100, 50))
        assert math.isclose(mapped_center.x(), 162.0)
        assert math.isclose(mapped_center.y(), 92.0)
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_render_plan_carries_default_scene_metadata(qapp):
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(400, 200),
        color=Qt.red,
    )
    try:
        source_image = _make_image(200, 100, Qt.green)
        image_id = uuid.uuid4()
        image_path = Path("scene-plan.png")
        harness.catalog.revision = 4
        harness.set_image(
            harness.qpane.original_image,
            path=image_path,
            image_id=image_id,
        )
        harness.viewport.zoom = 0.5
        harness.viewport.pan = QPointF(12.0, -8.0)
        harness.set_catalog_resolver(lambda _asset_key, _target_width: source_image)

        plan = harness.presenter.calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert plan.scene_id == default_scene_id(image_id)
        assert plan.scene_bounds.width == 400.0
        assert plan.scene_bounds.height == 200.0
        assert plan.content_bounds == plan.scene_bounds
        assert math.isclose(plan.zoom, 0.5)
        assert plan.current_pan == QPointF(12.0, -8.0)
        assert plan.qpane_rect == harness.qpane.rect()
        assert plan.physical_viewport_rect == QRectF(0, 0, 300, 200)
        assert len(plan.render_items) == 1
        assert len(plan.hit_test_items) == 1
        raster_item = plan.base_raster_item
        assert raster_item is plan.render_items[0]
        assert raster_item.source_image.size() == source_image.size()
        assert raster_item.source_image.pixelColor(0, 0) == source_image.pixelColor(
            0, 0
        )
        assert math.isclose(raster_item.pyramid_scale, 0.5)
        assert raster_item.strategy == RenderStrategy.DIRECT
        assert raster_item.render_hint_enabled is True
        assert raster_item.debug_draw_tile_grid is False
        assert raster_item.tiles_to_draw == ()
        assert raster_item.tile_size == harness.presenter.tile_manager.tile_size
        assert raster_item.tile_overlap == harness.presenter.tile_manager.tile_overlap
        assert raster_item.max_tile_cols == 0
        assert raster_item.max_tile_rows == 0
        assert raster_item.visible_tile_range is None
        assert raster_item.descriptor.layer_id == base_image_layer_id(image_id)
        assert raster_item.descriptor.source_revision == 4
        assert raster_item.asset_key.scene_id == default_scene_id(image_id)
        assert raster_item.asset_key.layer_id == base_image_layer_id(image_id)
        assert raster_item.asset_key.source_id == image_id
        assert raster_item.asset_key.source_kind == "catalog-image"
        assert raster_item.asset_key.source_revision == 4
        assert raster_item.asset_key.source_path == image_path
        assert raster_item.pyramid_asset_key == raster_item.asset_key
        mapped_center = raster_item.transform.map(QPointF(100, 50))
        assert math.isclose(mapped_center.x(), 162.0)
        assert math.isclose(mapped_center.y(), 92.0)
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_render_plan_carries_mask_scene_layers(qapp):
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(400, 200),
        color=Qt.red,
    )
    try:
        source_image = _make_image(200, 100, Qt.green)
        harness.viewport.zoom = 0.5
        harness.set_catalog_resolver(lambda _asset_key, _target_width: source_image)
        bottom_id = uuid.uuid4()
        top_id = uuid.uuid4()
        mask_image = QImage(400, 200, QImage.Format_Grayscale8)
        mask_image.fill(255)
        manager = StubMaskManager(
            {
                bottom_id: StubMaskLayer(mask_image, opacity=0.25),
                top_id: StubMaskLayer(mask_image, opacity=0.75),
            }
        )
        controller = StubMaskController({bottom_id: 4, top_id: 9})
        service = StubMaskService(manager, controller)
        harness.qpane.mask_service = service
        harness.qpane.sceneProviderRegistry().register_contribution(
            MaskServiceSceneProvider(service)
        )

        plan = harness.presenter.calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert len(plan.render_items) == 3
        base_item, bottom_item, top_item = plan.render_items
        assert base_item.descriptor.kind == LayerKind.IMAGE
        assert bottom_item.descriptor.kind == LayerKind.MASK
        assert top_item.descriptor.kind == LayerKind.MASK
        assert isinstance(bottom_item.descriptor.source, MaskLayerSource)
        assert isinstance(top_item.descriptor.source, MaskLayerSource)
        assert bottom_item.descriptor.source.mask_id == bottom_id
        assert top_item.descriptor.source.mask_id == top_id
        assert bottom_item.descriptor.source_revision == 4
        assert top_item.descriptor.source_revision == 9
        assert math.isclose(bottom_item.descriptor.opacity, 0.25)
        assert math.isclose(top_item.descriptor.opacity, 0.75)
        assert bottom_item.transform == base_item.transform
        assert top_item.transform == base_item.transform
        assert service.calls == [(bottom_id, 0.5), (top_id, 0.5)]
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_render_plan_uses_catalog_image_when_best_fit_missing(qapp):
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(128, 96),
        color=Qt.blue,
    )
    try:
        catalog_image = harness.catalog.base_image
        harness.qpane.original_image = QImage()
        harness.set_catalog_resolver(lambda _asset_key, _target_width: QImage())
        plan = harness.presenter.calculateRenderPlan(is_blank=False)
        assert plan is not None
        item = plan.base_raster_item
        assert item is not None
        assert item.source_image.size() == catalog_image.size()
        assert math.isclose(item.pyramid_scale, 1.0)
        assert item.strategy == RenderStrategy.DIRECT
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_render_plan_ignores_stale_widget_mirror(qapp):
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(128, 96),
        color=Qt.blue,
    )
    try:
        stale_image = _make_image(8, 8, Qt.red)
        harness.qpane.original_image = stale_image
        plan = harness.presenter.calculateRenderPlan(is_blank=False)
        assert plan is not None
        item = plan.base_raster_item
        assert item is not None
        assert item.source_image.size() == harness.catalog.base_image.size()
        assert (
            plan.content_snapshot.base_image_size == harness.catalog.base_image.size()
        )
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_current_content_snapshot_reuses_cached_resolution(qapp, monkeypatch):
    """Repeated content geometry lookups should not resolve the scene again."""
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(128, 96),
        color=Qt.blue,
    )
    try:
        presenter = harness.presenter
        original = presenter._resolve_active_scene_content
        calls = []

        def resolve_once():
            calls.append("resolve")
            return original()

        monkeypatch.setattr(presenter, "_resolve_active_scene_content", resolve_once)

        first = presenter.current_content_snapshot()
        second = presenter.current_content_snapshot()

        assert first is second
        assert calls == ["resolve"]

        harness.catalog.revision += 1
        third = presenter.current_content_snapshot()

        assert third is not None
        assert len(calls) == 2
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_calculate_render_plan_reuses_cached_active_content(qapp, monkeypatch):
    """Repeated paint planning should reuse active content while inputs are stable."""
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(128, 96),
        color=Qt.blue,
    )
    try:
        presenter = harness.presenter
        original = presenter._resolve_active_scene_content
        calls = []

        def resolve_once():
            calls.append("resolve")
            return original()

        monkeypatch.setattr(presenter, "_resolve_active_scene_content", resolve_once)

        first = presenter.calculateRenderPlan(is_blank=False)
        second = presenter.calculateRenderPlan(is_blank=False)

        assert first is not None
        assert second is not None
        assert first.content_snapshot == second.content_snapshot
        assert calls == ["resolve"]

        harness.catalog.revision += 1
        third = presenter.calculateRenderPlan(is_blank=False)

        assert third is not None
        assert len(calls) == 2
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_calculate_render_plan_reuses_cached_hit_test_projection(qapp, monkeypatch):
    """Repeated paint planning should not rebuild stable hit-test metadata."""
    harness = PresenterHarness(
        qpane_size=(300, 200),
        image_size=(128, 96),
        color=Qt.blue,
    )
    try:
        presenter = harness.presenter
        original = presenter._hit_test_items_for_scene
        calls = []

        def project_once(scene):
            calls.append("project")
            return original(scene)

        monkeypatch.setattr(presenter, "_hit_test_items_for_scene", project_once)

        first = presenter.calculateRenderPlan(is_blank=False)
        second = presenter.calculateRenderPlan(is_blank=False)

        assert first is not None
        assert second is not None
        assert first.hit_test_items is second.hit_test_items
        assert calls == ["project"]

        harness.catalog.revision += 1
        third = presenter.calculateRenderPlan(is_blank=False)

        assert third is not None
        assert len(calls) == 2
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_calculate_render_plan_enters_tile_mode_when_zoomed(qapp):
    harness = PresenterHarness(
        qpane_size=(256, 256),
        image_size=(2048, 2048),
        color=Qt.blue,
    )
    try:
        stub_manager = StubTileManager(tile_size=256, tile_overlap=0)
        presenter = harness.presenter
        presenter.tile_manager = stub_manager
        harness.viewport.zoom = 4.0
        harness.set_path(Path("tile.png"))
        plan = presenter.calculateRenderPlan(is_blank=False)
        assert plan is not None
        item = plan.base_raster_item
        assert item is not None
        assert item.strategy == RenderStrategy.TILE
        assert stub_manager.requested
        assert len(item.tiles_to_draw) == len(stub_manager.requested)
        assert stub_manager.cancelled is not None
        assert item.visible_tile_range is not None
        for identifier, tile_data in zip(stub_manager.requested, item.tiles_to_draw):
            assert isinstance(identifier, SceneLayerTileKey)
            assert identifier.asset_key.source_id == harness.qpane.currentImageID()
            assert identifier.asset_key.source_path == Path("tile.png")
            assert identifier.pyramid_asset_key == identifier.asset_key
            assert math.isclose(identifier.pyramid_scale, item.pyramid_scale)
            expected_pos = presenter.get_tile_draw_position(identifier)
            assert tile_data.draw_pos == expected_pos
        assert stub_manager.cancelled == frozenset(stub_manager.requested)
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_base_tile_range_matches_old_viewport_math(qapp):
    harness = PresenterHarness(
        qpane_size=(320, 240),
        image_size=(2048, 1536),
        color=Qt.blue,
    )
    try:
        stub_manager = StubTileManager(tile_size=256, tile_overlap=16)
        presenter = harness.presenter
        presenter.tile_manager = stub_manager
        harness.viewport.zoom = 4.0
        harness.viewport.pan = QPointF(96.0, -48.0)
        harness.set_path(Path("old-math.png"))

        plan = presenter.calculateRenderPlan(is_blank=False)

        assert plan is not None
        item = plan.base_raster_item
        assert item is not None
        assert item.strategy == RenderStrategy.TILE
        assert item.visible_tile_range == _old_qpane_visible_tile_range(
            source_size=item.source_image.size(),
            physical_viewport_rect=plan.physical_viewport_rect,
            zoom=plan.zoom,
            pan=plan.current_pan,
            pyramid_scale=item.pyramid_scale,
            tile_size=stub_manager.tile_size,
            tile_overlap=stub_manager.tile_overlap,
        )
        assert stub_manager.cancelled == frozenset(stub_manager.requested)
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_scene_render_plan_carries_tile_metadata(qapp):
    harness = PresenterHarness(
        qpane_size=(256, 256),
        image_size=(2048, 2048),
        color=Qt.blue,
    )
    try:
        stub_manager = StubTileManager(tile_size=256, tile_overlap=0)
        presenter = harness.presenter
        presenter.tile_manager = stub_manager
        harness.viewport.zoom = 4.0
        harness.set_path(Path("tile-scene.png"))
        plan = presenter.calculateRenderPlan(is_blank=False)
        assert plan is not None
        raster_item = plan.base_raster_item
        assert raster_item is not None
        assert raster_item.strategy == RenderStrategy.TILE
        assert stub_manager.requested
        assert len(raster_item.tiles_to_draw) == len(stub_manager.requested)
        assert raster_item.visible_tile_range is not None
        assert stub_manager.cancelled == frozenset(stub_manager.requested)
        for identifier, tile_data in zip(
            stub_manager.requested,
            raster_item.tiles_to_draw,
        ):
            assert isinstance(identifier, SceneLayerTileKey)
            assert identifier.asset_key.source_id == harness.qpane.currentImageID()
            assert identifier.asset_key.source_path == Path("tile-scene.png")
            assert identifier.pyramid_asset_key == identifier.asset_key
            assert math.isclose(identifier.pyramid_scale, raster_item.pyramid_scale)
            assert tile_data.draw_pos == presenter.get_tile_draw_position(identifier)
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_scene_render_plan_blank_returns_none(qapp):
    harness = PresenterHarness(qpane_size=(64, 64), image_size=(32, 32))
    try:
        plan = harness.presenter.calculateRenderPlan(is_blank=True)
        assert plan is None
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_handle_resize_invokes_expected_branch(monkeypatch, qapp):
    harness = PresenterHarness()
    try:
        presenter = harness.presenter
        fit_calls: list[str] = []
        pan_calls: list[QPointF] = []
        alloc_calls: list[str] = []
        monkeypatch.setattr(
            presenter.viewport, "setZoomFit", lambda: fit_calls.append("fit")
        )

        def fake_set_pan(value: QPointF) -> None:
            pan_calls.append(value)

        monkeypatch.setattr(presenter.viewport, "setPan", fake_set_pan)
        monkeypatch.setattr(
            presenter,
            "allocate_buffers",
            lambda: alloc_calls.append("alloc"),
        )
        presenter.viewport.zoom_mode = ViewportZoomMode.FIT
        presenter.handle_resize()
        assert fit_calls == ["fit"]
        assert alloc_calls == ["alloc"]
        fit_calls.clear()
        alloc_calls.clear()
        pan_calls.clear()
        presenter.viewport.zoom_mode = ViewportZoomMode.CUSTOM
        presenter.handle_resize()
        assert pan_calls and pan_calls[-1] == presenter.viewport.pan
        assert alloc_calls == ["alloc"]
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_paint_reallocates_when_buffer_size_stale(monkeypatch, qapp):
    harness = PresenterHarness(
        qpane_size=(160, 120),
        image_size=(64, 64),
        color=Qt.yellow,
    )
    try:
        harness.set_path(Path("stale.png"))
        presenter = harness.presenter

        class BufferGuardRenderer:
            def __init__(self) -> None:
                self._buffer = QImage(
                    32,
                    32,
                    QImage.Format_ARGB32_Premultiplied,
                )
                self._buffer.fill(Qt.black)
                self.allocations = 0

            def paint(self, state) -> None:
                self.state = state

            def get_base_buffer(self) -> QImage:
                return self._buffer

            def get_subpixel_pan_offset(self) -> QPointF:
                return QPointF(0, 0)

            def allocate_buffers(self, size, dpr):
                self.allocations += 1
                self._buffer = QImage(
                    size.width(),
                    size.height(),
                    QImage.Format_ARGB32_Premultiplied,
                )
                self._buffer.fill(Qt.black)

            def markDirty(self, dirty_rect=None):
                pass

        guard_renderer = BufferGuardRenderer()
        monkeypatch.setattr(presenter, "renderer", guard_renderer)
        presenter.paint(
            is_blank=False,
            content_overlays={},
            overlays_suspended=True,
            draw_tool_overlay=None,
        )
        assert guard_renderer.allocations == 1
        expected_size = presenter._qpane_physical_size()
        assert guard_renderer.get_base_buffer().size() == expected_size
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_paint_skips_allocation_when_buffer_size_matches(monkeypatch, qapp):
    harness = PresenterHarness(
        qpane_size=(128, 96),
        image_size=(64, 64),
        color=Qt.cyan,
    )
    try:
        harness.set_path(Path("fresh.png"))
        presenter = harness.presenter
        target_size = presenter._qpane_physical_size()

        class BufferGuardRenderer:
            def __init__(self) -> None:
                self._buffer = QImage(
                    target_size.width(),
                    target_size.height(),
                    QImage.Format_ARGB32_Premultiplied,
                )
                self._buffer.fill(Qt.black)
                self.allocations = 0

            def paint(self, state) -> None:
                self.state = state

            def get_base_buffer(self) -> QImage:
                return self._buffer

            def get_subpixel_pan_offset(self) -> QPointF:
                return QPointF(0, 0)

            def allocate_buffers(self, size, dpr):
                self.allocations += 1

            def markDirty(self, dirty_rect=None):
                pass

        guard_renderer = BufferGuardRenderer()
        monkeypatch.setattr(presenter, "renderer", guard_renderer)
        presenter.paint(
            is_blank=False,
            content_overlays={},
            overlays_suspended=True,
            draw_tool_overlay=None,
        )
        assert guard_renderer.allocations == 0
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_paint_restores_transform_before_tool_overlay(monkeypatch, qapp):
    harness = PresenterHarness()
    try:
        presenter = harness.presenter

        class StubRenderer:
            def __init__(self) -> None:
                self._buffer = _make_image(16, 16, Qt.green)

            def paint(self, state) -> None:
                self.state = state

            def get_base_buffer(self) -> QImage:
                return self._buffer

            def get_subpixel_pan_offset(self) -> QPointF:
                return QPointF(0.5, 0.25)

            def allocate_buffers(self, size, dpr):
                self._buffer = _make_image(
                    size.width(),
                    size.height(),
                    Qt.green,
                )

        stub_renderer = StubRenderer()
        monkeypatch.setattr(presenter, "renderer", stub_renderer)
        monkeypatch.setattr(
            presenter,
            "calculateRenderPlan",
            lambda **_: object(),
        )
        observed_transforms = []

        def capture_tool_overlay(painter):
            observed_transforms.append(painter.transform())

        presenter.paint(
            is_blank=False,
            content_overlays={},
            overlays_suspended=True,
            draw_tool_overlay=capture_tool_overlay,
        )
        assert observed_transforms
        assert observed_transforms[-1].isIdentity()
    finally:
        _cleanup_qpane(harness.qpane, qapp)


def test_presenter_strategy_threshold_uses_physical_viewport(monkeypatch, qapp):
    harness = PresenterHarness(
        qpane_size=(200, 120),
        image_size=(1024, 1024),
        color=Qt.darkGray,
    )
    try:
        stub_manager = StubTileManager(tile_size=256, tile_overlap=0)
        presenter = harness.presenter
        presenter.tile_manager = stub_manager
        image_width = harness.qpane.original_image.width()
        image_height = harness.qpane.original_image.height()
        for dpr in (1.0, 2.0):
            harness.set_device_pixel_ratio(dpr)
            physical_width = harness.qpane.width() * dpr
            physical_height = harness.qpane.height() * dpr
            threshold_zoom = min(
                physical_width / image_width, physical_height / image_height
            )
            harness.viewport.zoom = threshold_zoom * 0.95
            plan = presenter.calculateRenderPlan(is_blank=False)
            assert plan is not None
            item = plan.base_raster_item
            assert item is not None
            assert item.strategy == RenderStrategy.DIRECT
            harness.viewport.zoom = threshold_zoom * 1.05
            plan = presenter.calculateRenderPlan(is_blank=False)
            assert plan is not None
            item = plan.base_raster_item
            assert item is not None
            assert item.strategy == RenderStrategy.TILE
    finally:
        _cleanup_qpane(harness.qpane, qapp)
