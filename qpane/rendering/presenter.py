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

"""Rendering presenter responsible for QPane's drawing pipeline."""

from __future__ import annotations


import logging

import uuid
from dataclasses import dataclass
from math import isclose
from pathlib import Path

from typing import TYPE_CHECKING, Callable, Mapping


from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, QSizeF

from PySide6.QtGui import QImage, QPainter, QTransform, Qt

from PySide6.QtWidgets import QWidget


from .coordinates import CoordinateContext, PanelHitTest

from .render import Renderer

from .tiles import TileManager

from .visibility import visible_source_rect_for_layer

from .viewport import Viewport, ViewportZoomMode
from ..scene.default_scene import DefaultCatalogSceneProvider
from ..scene.identity import (
    SceneLayerAssetKey,
    SceneLayerTileKey,
    base_image_layer_id,
    default_catalog_asset_key,
    mask_layer_asset_key,
    scene_image_asset_key,
)
from ..scene.model import (
    ClipCoordinateSpace,
    LayerClip,
    LayerDescriptor,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
    SceneKind,
)
from ..scene.placeholder_scene import build_placeholder_scene
from ..scene.providers import SceneContribution, SceneResolver
from ..scene.render_plan import (
    MaskLayerRenderItem,
    RasterLayerRenderItem,
    RenderStrategy,
    SceneContentSnapshot,
    SceneHitTestItem,
    SceneLayerHitTestResult,
    SceneRenderPlan,
    SceneRenderItem,
    TileRenderData,
)
from ..scene.sources import (
    CatalogImageSource,
    MaskLayerSource,
    PlaceholderImageSource,
)
from ..types import OverlayState, QPaneSceneOverlayLayer, QPaneSceneOverlayState


if TYPE_CHECKING:
    from ..cache.registry import CacheRegistry
    from ..catalog import ImageCatalog
    from ..concurrency import TaskExecutorProtocol
    from ..core import OverlayDrawFn, SceneOverlayDrawFn
    from ..qpane import QPane
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _StaticSceneProvider:
    """Return a prebuilt scene contribution to the resolver."""

    contribution: SceneContribution

    def scene_contribution(self) -> SceneContribution:
        """Return the stored contribution."""
        return self.contribution


@dataclass(frozen=True, slots=True)
class _BaseRasterSnapshot:
    """Behavior-preserving raster planning data shared by old and new snapshots."""

    base_image: QImage
    source_image: QImage
    scene: SceneDescriptor
    image_id: uuid.UUID | None
    source_path: Path | None
    source_revision: int
    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey
    content_snapshot: SceneContentSnapshot
    pyramid_scale: float
    transform: QTransform
    zoom: float
    strategy: RenderStrategy
    render_hint_enabled: bool
    debug_draw_tile_grid: bool
    tiles_to_draw: tuple[TileRenderData, ...]
    tile_size: int
    tile_overlap: int
    max_tile_cols: int
    max_tile_rows: int
    qpane_rect: QRect
    current_pan: QPointF
    physical_viewport_rect: QRectF
    visible_scene_rect: QRectF
    visible_tile_range: tuple[int, int, int, int] | None


@dataclass(frozen=True, slots=True)
class _RasterPlanningResult:
    """Raster render item plus the visible tile keys requested while building it."""

    item: RasterLayerRenderItem | None
    visible_tile_keys: frozenset[SceneLayerTileKey]


@dataclass(frozen=True, slots=True)
class RasterLayerGeometry:
    """Geometry needed to map layer source pixels into the panel."""

    scene_id: uuid.UUID
    layer_id: uuid.UUID
    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey
    pyramid_scale: float
    transform: QTransform
    placement: LayerPlacement
    clip: LayerClip | None
    source_size: QSize
    tile_size: int
    tile_overlap: int
    visible_source_rect: QRectF

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry values from caller-owned state."""
        object.__setattr__(self, "transform", QTransform(self.transform))
        object.__setattr__(self, "source_size", QSize(self.source_size))
        object.__setattr__(
            self,
            "visible_source_rect",
            QRectF(self.visible_source_rect),
        )


@dataclass(frozen=True, slots=True)
class _ActiveSceneContent:
    """Resolved scene content used as the authoritative render source."""

    scene: SceneDescriptor
    base_image: QImage
    image_id: uuid.UUID | None
    source_path: Path | None
    source_revision: int
    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey


class RenderingPresenter:
    """Encapsulate rendering-specific state and QWidget hooks for QPane."""

    def __init__(
        self,
        *,
        qpane: "QPane",
        catalog: "ImageCatalog",
        cache_registry: "CacheRegistry" | None,
        executor: "TaskExecutorProtocol",
    ) -> None:
        """Compose viewport/tile/renderer collaborators owned by the presenter."""
        self._qpane = qpane
        self._catalog = catalog
        self.viewport = Viewport(qpane, qpane.settings)
        self.tile_manager = TileManager(qpane.settings, parent=qpane, executor=executor)
        if cache_registry is not None:
            cache_registry.attach_tile_manager(self.tile_manager)
        self.renderer = Renderer(qpane)
        self._scene_providers = qpane.sceneProviderRegistry()
        self._source_resolvers = qpane.layerSourceResolverRegistry()
        self._last_view_size = QSize()
        self._last_device_pixel_ratio = float(qpane.devicePixelRatioF())
        self._placeholder_content_provider: Callable[[], object | None] | None = None
        self._cached_content_key: tuple[object, ...] | None = None
        self._cached_active_content: _ActiveSceneContent | None = None
        self._cached_content_snapshot: SceneContentSnapshot | None = None
        self._cached_hit_test_key: tuple[object, ...] | None = None
        self._cached_hit_test_items: tuple[SceneHitTestItem, ...] = ()

    def set_placeholder_content_provider(
        self, provider: Callable[[], object | None]
    ) -> None:
        """Install the catalog-owned placeholder content provider."""
        self._placeholder_content_provider = provider

    def calculateRenderPlan(
        self,
        *,
        use_pan: QPointF | None = None,
        is_blank: bool = False,
    ) -> SceneRenderPlan | None:
        """Build the active scene render plan for the current viewport."""
        snapshot = self._calculate_base_raster_snapshot(
            use_pan=use_pan,
            is_blank=is_blank,
        )
        if snapshot is None:
            return None
        scene = snapshot.scene
        raster_items = self._build_raster_render_items(scene, snapshot)
        if not raster_items:
            return None
        base_item = self._base_raster_item_from_items(raster_items)
        mask_items = self._build_mask_render_items(
            scene,
            snapshot,
            base_item,
        )
        return SceneRenderPlan(
            scene_id=scene.scene_id,
            scene_bounds=scene.bounds,
            content_bounds=scene.bounds,
            content_snapshot=snapshot.content_snapshot,
            zoom=snapshot.zoom,
            current_pan=snapshot.current_pan,
            qpane_rect=snapshot.qpane_rect,
            physical_viewport_rect=snapshot.physical_viewport_rect,
            render_items=(*raster_items, *mask_items),
            hit_test_items=self._cached_hit_test_items_for_scene(scene),
        )

    def paint(
        self,
        *,
        is_blank: bool,
        content_overlays: Mapping[str, "OverlayDrawFn"],
        scene_overlays: Mapping[str, "SceneOverlayDrawFn"] | None = None,
        overlays_suspended: bool,
        draw_tool_overlay: Callable[[QPainter], None] | None,
    ) -> SceneRenderPlan | None:
        """Render the current frame and return the scene render plan used."""
        active_scene_overlays = scene_overlays or {}
        if is_blank:
            render_plan = (
                self.calculateRenderPlan(is_blank=is_blank)
                if content_overlays or active_scene_overlays
                else None
            )
            painter = QPainter(self._qpane)
            try:
                painter.fillRect(self._qpane.rect(), Qt.transparent)
                self._draw_content_overlays(
                    painter,
                    render_plan,
                    content_overlays,
                    overlays_suspended=overlays_suspended,
                )
                self._draw_scene_overlays(
                    painter,
                    render_plan,
                    active_scene_overlays,
                    overlays_suspended=overlays_suspended,
                )
            finally:
                painter.end()
            return render_plan
        render_plan = self.calculateRenderPlan(is_blank=is_blank)
        if render_plan:
            self._ensure_buffer_matches_widget()
            self.renderer.paint(render_plan)
        painter = QPainter(self._qpane)
        transform_applied = False
        try:
            base_buffer = self.renderer.get_base_buffer()
            if base_buffer:
                offset = self.renderer.get_subpixel_pan_offset()
                if offset != QPointF(0, 0):
                    context = CoordinateContext(self._qpane)
                    logical_offset = context.physical_to_logical(offset)
                    painter.translate(logical_offset)
                    transform_applied = True
                painter.drawImage(0, 0, base_buffer)
            self._draw_content_overlays(
                painter,
                render_plan,
                content_overlays,
                overlays_suspended=overlays_suspended,
            )
            self._draw_scene_overlays(
                painter,
                render_plan,
                active_scene_overlays,
                overlays_suspended=overlays_suspended,
            )
            if transform_applied:
                painter.resetTransform()
            if draw_tool_overlay and not is_blank:
                draw_tool_overlay(painter)
        finally:
            painter.end()
        return render_plan

    def _draw_content_overlays(
        self,
        painter: QPainter,
        render_plan: SceneRenderPlan | None,
        content_overlays: Mapping[str, "OverlayDrawFn"],
        *,
        overlays_suspended: bool,
    ) -> None:
        """Draw public overlays from the base raster item when available."""
        if render_plan is None or overlays_suspended or not content_overlays:
            return
        overlay_state = self._build_overlay_state(render_plan)
        if overlay_state is None:
            return
        for draw_overlay in content_overlays.values():
            draw_overlay(painter, overlay_state)

    def _build_overlay_state(self, render_plan: SceneRenderPlan) -> OverlayState | None:
        """Project a scene render plan onto the public OverlayState surface."""
        base_item = render_plan.base_raster_item
        if base_item is None:
            return None
        return OverlayState(
            zoom=render_plan.zoom,
            qpane_rect=render_plan.qpane_rect,
            source_image=base_item.source_image,
            transform=base_item.transform,
            current_pan=render_plan.current_pan,
            physical_viewport_rect=render_plan.physical_viewport_rect,
        )

    def _draw_scene_overlays(
        self,
        painter: QPainter,
        render_plan: SceneRenderPlan | None,
        scene_overlays: Mapping[str, "SceneOverlayDrawFn"],
        *,
        overlays_suspended: bool,
    ) -> None:
        """Draw public scene overlays from rendered scene-layer geometry."""
        if render_plan is None or overlays_suspended or not scene_overlays:
            return
        overlay_state = self._build_scene_overlay_state(render_plan)
        if overlay_state is None:
            return
        for draw_overlay in scene_overlays.values():
            draw_overlay(painter, overlay_state)

    def _build_scene_overlay_state(
        self, render_plan: SceneRenderPlan
    ) -> QPaneSceneOverlayState | None:
        """Project a render plan onto the public scene-overlay surface."""
        scene_getter = getattr(self._qpane, "currentScene", None)
        if not callable(scene_getter):
            return None
        scene = scene_getter()
        if scene is None or scene.scene_id != render_plan.scene_id:
            return None
        layers_by_id = {layer.layer_id: layer for layer in scene.layers}
        layers: list[QPaneSceneOverlayLayer] = []
        for item in render_plan.render_items:
            if not isinstance(item, RasterLayerRenderItem):
                continue
            public_layer = layers_by_id.get(item.descriptor.layer_id)
            if public_layer is None:
                continue
            source_size = item.source_image.size()
            source_rect = QRectF(
                0.0,
                0.0,
                float(source_size.width()),
                float(source_size.height()),
            )
            layers.append(
                QPaneSceneOverlayLayer(
                    layer_id=public_layer.layer_id,
                    image_id=public_layer.image_id,
                    role=public_layer.role,
                    metadata=public_layer.metadata,
                    placement=QRectF(public_layer.placement),
                    source_size=source_size,
                    transform=item.transform,
                    panel_bounds=item.transform.mapRect(source_rect),
                    visible=item.descriptor.visible,
                )
            )
        if not layers:
            return None
        return QPaneSceneOverlayState(
            zoom=render_plan.zoom,
            qpane_rect=render_plan.qpane_rect,
            physical_viewport_rect=render_plan.physical_viewport_rect,
            composition_id=scene.composition_id,
            scene_id=render_plan.scene_id,
            scene_bounds=QRectF(
                render_plan.scene_bounds.x,
                render_plan.scene_bounds.y,
                render_plan.scene_bounds.width,
                render_plan.scene_bounds.height,
            ),
            layers=tuple(layers),
        )

    def mark_dirty(self, dirty_rect: QRect | QRectF | None = None) -> None:
        """Forward dirty-region notifications to the renderer."""
        self.renderer.markDirty(dirty_rect)

    def allocate_buffers(self) -> None:
        """Allocate the renderer buffers to match the current widget size."""
        self._refresh_backing_buffers()

    def ensure_view_alignment(self, *, force: bool = False) -> None:
        """Reapply FIT/custom zoom and buffers when the qpane geometry changes."""
        current_size = self._qpane.size()
        current_dpr = float(self._qpane.devicePixelRatioF())
        dpr_changed = not isclose(
            current_dpr, self._last_device_pixel_ratio, rel_tol=1e-9, abs_tol=1e-9
        )
        if not force and current_size == self._last_view_size and not dpr_changed:
            return
        zoom_mode = self.viewport.get_zoom_mode()
        if zoom_mode == ViewportZoomMode.FIT:
            self.viewport.setZoomFit()
        else:
            self.viewport.setPan(self.viewport.pan)
        self.allocate_buffers()
        self._last_view_size = QSize(current_size)
        self._last_device_pixel_ratio = current_dpr

    def physical_viewport_rect(self) -> QRectF:
        """Return the viewport rectangle expressed in device pixels."""
        context = CoordinateContext(self._qpane)
        return context.logical_to_physical(QRectF(self._qpane.rect()))

    def panel_to_image_point(self, panel_pos: QPoint) -> QPoint | None:
        """Convert a panel coordinate into image space using the viewport."""
        return self.viewport.panel_to_content_point(panel_pos)

    def panel_hit_test(self, panel_pos: QPoint) -> PanelHitTest | None:
        """Return hit-test metadata for panel coordinates via the viewport."""
        return self.viewport.panel_hit_test(panel_pos)

    def scene_hit_test(self, panel_pos: QPoint) -> SceneLayerHitTestResult | None:
        """Return the top scene layer under ``panel_pos`` when one matches."""
        plan = self.calculateRenderPlan(
            is_blank=getattr(self._qpane, "_is_blank", False)
        )
        if plan is None:
            return None
        panel_point = QPointF(panel_pos)
        for item in reversed(plan.render_items):
            result = self._hit_test_render_item(plan, item, panel_point)
            if result is not None:
                return result
        return None

    def image_to_panel_point(self, image_point: QPoint) -> QPointF | None:
        """Project an image-space coordinate into the widget."""
        return self.viewport.content_to_panel_point(image_point)

    def handle_resize(self) -> None:
        """Respond to QWidget resize events."""
        if self.viewport.get_zoom_mode() == ViewportZoomMode.FIT:
            self._handle_resize_fit_mode()
        else:
            self._handle_resize_custom_mode()
        self.allocate_buffers()

    def minimum_size_hint(self) -> QSize:
        """Return the safe minimum widget size for the current image."""
        content_snapshot = self.current_content_snapshot()
        if content_snapshot is None:
            base_hint = QWidget.minimumSizeHint(self._qpane)
            if base_hint.isValid() and not base_hint.isNull():
                return base_hint
            return QSize(1, 1)
        safe_min_zoom = getattr(self._qpane.settings, "safe_min_zoom", 1e-3)
        min_zoom = max(self.viewport.min_zoom(), safe_min_zoom)
        base_size = content_snapshot.base_image_size
        min_width = max(1, int(round(base_size.width() * min_zoom)))
        min_height = max(1, int(round(base_size.height() * min_zoom)))
        return QSize(min_width, min_height)

    def current_content_snapshot(self) -> SceneContentSnapshot | None:
        """Return geometry for the current rendered content when available."""
        active_content = self._cached_active_scene_content()
        if active_content is None:
            return None
        if self._cached_content_snapshot is None:
            self._cached_content_snapshot = self._content_snapshot_for_active_content(
                active_content
            )
        return self._cached_content_snapshot

    def current_scene_descriptor(self) -> SceneDescriptor | None:
        """Return the active scene descriptor without building render items."""
        active_content = self._cached_active_scene_content()
        return active_content.scene if active_content is not None else None

    def invalidate_content_cache(self) -> None:
        """Drop cached active scene/content geometry."""
        self._cached_content_key = None
        self._cached_active_content = None
        self._cached_content_snapshot = None
        self._cached_hit_test_key = None
        self._cached_hit_test_items = ()

    def has_renderable_content(self) -> bool:
        """Return True when the presenter can resolve content for rendering."""
        return self.current_content_snapshot() is not None

    def content_rect(self) -> QRect:
        """Return the current base content rectangle in content coordinates."""
        snapshot = self.current_content_snapshot()
        if snapshot is None:
            return QRect()
        return QRect(QPoint(0, 0), snapshot.base_image_size)

    def _qpane_physical_size(self) -> QSize:
        """Return the qpane's current size expressed in device pixels."""
        context = CoordinateContext(self._qpane)
        logical_size = QSizeF(self._qpane.size())
        return context.logical_to_physical(logical_size).toSize()

    def _refresh_backing_buffers(self) -> None:
        """Rebuild renderer buffers based on the current widget DPR and size."""
        physical_size = self._qpane_physical_size()
        dpr = self._qpane.devicePixelRatioF()
        self.renderer.allocate_buffers(physical_size, dpr)

    def _ensure_buffer_matches_widget(self) -> None:
        """Reallocate renderer buffers when the widget size has changed."""
        base_buffer = self.renderer.get_base_buffer()
        if base_buffer is None:
            self.allocate_buffers()
            return
        expected_size = self._qpane_physical_size()
        if base_buffer.size() != expected_size:
            self.allocate_buffers()

    # Internal helpers

    def get_tile_draw_position(self, key: SceneLayerTileKey) -> QPointF:
        """Return the upper-left draw position for ``key`` in source coords."""
        stride = self.tile_manager.tile_size - self.tile_manager.tile_overlap
        draw_x = key.col * stride
        draw_y = key.row * stride
        return QPointF(draw_x, draw_y)

    def dirty_rect_for_tile_key(self, key: SceneLayerTileKey) -> QRect | None:
        """Return the panel dirty rect for a visible ready tile."""
        for geometry in self._raster_layer_geometries(
            is_blank=getattr(self._qpane, "_is_blank", False)
        ):
            if geometry.asset_key != key.asset_key:
                continue
            if geometry.pyramid_asset_key != key.pyramid_asset_key:
                return None
            if abs(key.pyramid_scale - geometry.pyramid_scale) > 1e-6:
                return None
            source_rect = QRectF(
                self.get_tile_draw_position(key),
                QSizeF(geometry.tile_size, geometry.tile_size),
            )
            visible_source_rect = source_rect.intersected(geometry.visible_source_rect)
            if visible_source_rect.isEmpty():
                return None
            return (
                geometry.transform.mapRect(visible_source_rect)
                .adjusted(
                    -1,
                    -1,
                    1,
                    1,
                )
                .toAlignedRect()
            )
        return None

    def _calculate_visible_scene_rect(
        self,
        *,
        scene: SceneDescriptor,
        zoom: float,
        current_pan: QPointF,
        physical_viewport_rect: QRectF,
    ) -> QRectF:
        """Compute the scene-space rectangle visible through the viewport."""
        safe_zoom = zoom if not isclose(zoom, 0.0) else 1.0
        viewport_center = QPointF(physical_viewport_rect.center())
        scene_center = QPointF(
            scene.bounds.x + scene.bounds.width / 2.0,
            scene.bounds.y + scene.bounds.height / 2.0,
        )
        top_left_scene = (
            physical_viewport_rect.topLeft() - viewport_center - current_pan
        ) / safe_zoom + scene_center
        bottom_right_scene = (
            physical_viewport_rect.bottomRight() - viewport_center - current_pan
        ) / safe_zoom + scene_center
        return QRectF(top_left_scene, bottom_right_scene).normalized()

    def _calculate_tile_range_for_source_rect(
        self,
        *,
        source_rect: QRectF,
        tile_size: int,
        tile_overlap: int,
        max_cols: int,
        max_rows: int,
    ) -> tuple[int, int, int, int]:
        """Compute inclusive tile bounds for a source-space visible rectangle."""
        if source_rect.isEmpty() or max_cols <= 0 or max_rows <= 0:
            return 0, -1, 0, -1
        stride = tile_size - tile_overlap
        if stride <= 0:
            logger.error(
                "Tile stride is non-positive; size=%s overlap=%s max_cols=%s max_rows=%s",
                tile_size,
                tile_overlap,
                max_cols,
                max_rows,
            )
            return 0, -1, 0, -1
        start_col = max(0, int(source_rect.left() / stride) - 1)
        start_row = max(0, int(source_rect.top() / stride) - 1)
        end_col = min(max_cols - 1, int(source_rect.right() / stride) + 1)
        end_row = min(max_rows - 1, int(source_rect.bottom() / stride) + 1)
        if start_col > end_col or start_row > end_row:
            return 0, -1, 0, -1
        return start_row, end_row, start_col, end_col

    def _raster_layer_geometries(
        self,
        *,
        use_pan: QPointF | None = None,
        is_blank: bool = False,
    ) -> tuple[RasterLayerGeometry, ...]:
        """Resolve active raster layer geometry without paint payloads."""
        if is_blank:
            return ()
        active_content = self._cached_active_scene_content()
        if active_content is None:
            return ()
        content_snapshot = self._content_snapshot_for_active_content(active_content)
        current_pan = use_pan if use_pan is not None else self.viewport.pan
        physical_viewport_rect = self.physical_viewport_rect()
        visible_scene_rect = self._calculate_visible_scene_rect(
            scene=active_content.scene,
            zoom=self.viewport.zoom,
            current_pan=current_pan,
            physical_viewport_rect=physical_viewport_rect,
        )
        qpane_rect = QRectF(self._qpane.rect())
        geometries: list[RasterLayerGeometry] = []
        base_layer = self._first_image_layer(active_content.scene)
        for layer in active_content.scene.layers:
            if not layer.visible or layer.kind != LayerKind.IMAGE:
                continue
            if base_layer is not None and layer.layer_id == base_layer.layer_id:
                geometry = self._base_raster_geometry(
                    active_content=active_content,
                    layer=layer,
                    content_snapshot=content_snapshot,
                    current_pan=current_pan,
                    visible_scene_rect=visible_scene_rect,
                    qpane_rect=qpane_rect,
                )
            else:
                geometry = self._additional_raster_geometry(
                    scene=active_content.scene,
                    layer=layer,
                    content_snapshot=content_snapshot,
                    current_pan=current_pan,
                    visible_scene_rect=visible_scene_rect,
                    qpane_rect=qpane_rect,
                )
            if geometry is not None:
                geometries.append(geometry)
        return tuple(geometries)

    def _base_raster_geometry(
        self,
        *,
        active_content: _ActiveSceneContent,
        layer: LayerDescriptor,
        content_snapshot: SceneContentSnapshot,
        current_pan: QPointF,
        visible_scene_rect: QRectF,
        qpane_rect: QRectF,
    ) -> RasterLayerGeometry | None:
        """Resolve geometry for the active base image layer."""
        base_image = active_content.base_image
        target_width = base_image.width() * self.viewport.zoom
        source_image = (
            self._catalog.getBestFitImageForAsset(
                active_content.pyramid_asset_key,
                target_width,
            )
            if active_content.image_id is not None
            else base_image
        )
        if source_image is None or source_image.isNull():
            source_image = base_image
        pyramid_scale = (
            source_image.width() / base_image.width() if base_image.width() > 0 else 1.0
        )
        transform = self.viewport.get_transform(
            source_image.size(),
            pyramid_scale,
            pan_override=current_pan,
            content_snapshot=content_snapshot,
        )
        visibility = visible_source_rect_for_layer(
            scene_bounds=active_content.scene.bounds,
            layer_placement=layer.placement,
            source_size=source_image.size(),
            visible_scene_rect=visible_scene_rect,
            clip=layer.clip,
            viewport_rect=qpane_rect,
            item_transform=transform,
        )
        if visibility is None:
            return None
        return RasterLayerGeometry(
            scene_id=active_content.scene.scene_id,
            layer_id=layer.layer_id,
            asset_key=active_content.asset_key,
            pyramid_asset_key=active_content.pyramid_asset_key,
            pyramid_scale=pyramid_scale,
            transform=transform,
            placement=layer.placement,
            clip=layer.clip,
            source_size=source_image.size(),
            tile_size=self.tile_manager.tile_size,
            tile_overlap=self.tile_manager.tile_overlap,
            visible_source_rect=visibility.source_rect,
        )

    def _additional_raster_geometry(
        self,
        *,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        content_snapshot: SceneContentSnapshot,
        current_pan: QPointF,
        visible_scene_rect: QRectF,
        qpane_rect: QRectF,
    ) -> RasterLayerGeometry | None:
        """Resolve geometry for a non-base image layer."""
        full_image = self._source_image_for_layer(layer)
        if full_image is None or full_image.isNull():
            return None
        asset_key = self._render_asset_key_for_image_layer(scene, layer)
        pyramid_asset_key = self._pyramid_asset_key_for_image_layer(scene, layer)
        if pyramid_asset_key is None:
            return None
        source_image = self._best_fit_image_for_layer(
            layer,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            full_image=full_image,
            target_width=layer.placement.width * self.viewport.zoom,
        )
        pyramid_scale = (
            source_image.width() / full_image.width() if full_image.width() > 0 else 1.0
        )
        transform = self._transform_for_placed_geometry(
            scene=scene,
            layer=layer,
            source_size=source_image.size(),
            content_snapshot=content_snapshot,
            current_pan=current_pan,
        )
        visibility = visible_source_rect_for_layer(
            scene_bounds=scene.bounds,
            layer_placement=layer.placement,
            source_size=source_image.size(),
            visible_scene_rect=visible_scene_rect,
            clip=layer.clip,
            viewport_rect=qpane_rect,
            item_transform=transform,
        )
        if visibility is None:
            return None
        return RasterLayerGeometry(
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            pyramid_scale=pyramid_scale,
            transform=transform,
            placement=layer.placement,
            clip=layer.clip,
            source_size=source_image.size(),
            tile_size=self.tile_manager.tile_size,
            tile_overlap=self.tile_manager.tile_overlap,
            visible_source_rect=visibility.source_rect,
        )

    def _handle_resize_fit_mode(self) -> None:
        """Keep the viewport zoom aligned with the available widget size in FIT mode."""
        self.viewport.setZoomFit()

    def _handle_resize_custom_mode(self) -> None:
        """Reapply the current pan so it is clamped after a custom-mode resize."""
        self.viewport.setPan(self.viewport.pan)

    def _calculate_base_raster_snapshot(
        self,
        *,
        use_pan: QPointF | None,
        is_blank: bool,
    ) -> _BaseRasterSnapshot | None:
        """Resolve the current catalog image into behavior-preserving raster data."""
        if is_blank:
            return None
        active_content = self._cached_active_scene_content()
        if active_content is None:
            return None
        base_image = active_content.base_image
        content_snapshot = self._content_snapshot_for_active_content(active_content)
        base_layer = self._first_image_layer(active_content.scene)
        if base_layer is None:
            return None
        target_width = base_layer.placement.width * self.viewport.zoom
        source_image = (
            self._catalog.getBestFitImageForAsset(
                active_content.pyramid_asset_key, target_width
            )
            if active_content.image_id is not None
            else base_image
        )
        if source_image is None or source_image.isNull():
            source_image = base_image
        pyramid_scale = (
            source_image.width() / base_image.width() if base_image.width() > 0 else 1.0
        )
        canvas_size_physical = (
            QSizeF(base_layer.placement.width, base_layer.placement.height)
            * self.viewport.zoom
        )
        physical_viewport_rect = self.physical_viewport_rect()
        viewport_size_physical = QSizeF(physical_viewport_rect.size())
        strategy = RenderStrategy.DIRECT
        if (
            canvas_size_physical.width() > viewport_size_physical.width()
            or canvas_size_physical.height() > viewport_size_physical.height()
        ):
            strategy = RenderStrategy.TILE
        pan_value = use_pan if use_pan is not None else self.viewport.pan
        uses_default_base_tile_math = self._uses_default_base_tile_math(
            scene=active_content.scene,
            layer=base_layer,
            full_image=base_image,
        )
        if uses_default_base_tile_math:
            transform = self.viewport.get_transform(
                source_image.size(),
                pyramid_scale,
                pan_override=use_pan,
                content_snapshot=content_snapshot,
            )
        else:
            transform = self._transform_for_placed_geometry(
                scene=active_content.scene,
                layer=base_layer,
                source_size=source_image.size(),
                content_snapshot=content_snapshot,
                current_pan=pan_value,
            )
        render_hint_enabled = self.viewport.zoom < self.viewport.nativeZoom() * 2.0
        visible_scene_rect = self._calculate_visible_scene_rect(
            scene=active_content.scene,
            zoom=self.viewport.zoom,
            current_pan=pan_value,
            physical_viewport_rect=physical_viewport_rect,
        )
        debug_draw_tile_grid = self._qpane.settings.draw_tile_grid
        tile_size = self.tile_manager.tile_size
        tile_overlap = self.tile_manager.tile_overlap
        max_cols = 0
        max_rows = 0
        tiles_to_draw: list[TileRenderData] = []
        visible_range: tuple[int, int, int, int] | None = None
        if strategy == RenderStrategy.TILE:
            max_cols, max_rows = self.tile_manager.calculate_grid_dimensions(
                source_image.width(), source_image.height()
            )
            if self._uses_default_base_tile_math(
                scene=active_content.scene,
                layer=base_layer,
                full_image=base_image,
            ):
                visible_source_rect = self._calculate_default_visible_source_rect(
                    source_size=source_image.size(),
                    pyramid_scale=pyramid_scale,
                    current_pan=pan_value,
                    physical_viewport_rect=physical_viewport_rect,
                )
            else:
                visibility = visible_source_rect_for_layer(
                    scene_bounds=active_content.scene.bounds,
                    layer_placement=base_layer.placement,
                    source_size=source_image.size(),
                    visible_scene_rect=visible_scene_rect,
                    clip=base_layer.clip,
                    viewport_rect=QRectF(self._qpane.rect()),
                    item_transform=transform,
                )
                visible_source_rect = (
                    visibility.source_rect if visibility is not None else QRectF()
                )
            visible_range = self._calculate_tile_range_for_source_rect(
                source_rect=visible_source_rect,
                tile_size=tile_size,
                tile_overlap=tile_overlap,
                max_cols=max_cols,
                max_rows=max_rows,
            )
            start_row, end_row, start_col, end_col = visible_range
            for row in range(start_row, end_row + 1):
                for col in range(start_col, end_col + 1):
                    tile_key = SceneLayerTileKey(
                        asset_key=active_content.asset_key,
                        pyramid_asset_key=active_content.pyramid_asset_key,
                        pyramid_scale=pyramid_scale,
                        row=row,
                        col=col,
                    )
                    tile_image = self.tile_manager.get_tile(tile_key, source_image)
                    if tile_image:
                        draw_pos = self.get_tile_draw_position(tile_key)
                        tiles_to_draw.append(TileRenderData(tile_image, draw_pos))
        return _BaseRasterSnapshot(
            base_image=base_image,
            source_image=source_image,
            scene=active_content.scene,
            image_id=active_content.image_id,
            source_path=active_content.source_path,
            source_revision=active_content.source_revision,
            asset_key=active_content.asset_key,
            pyramid_asset_key=active_content.pyramid_asset_key,
            content_snapshot=content_snapshot,
            pyramid_scale=pyramid_scale,
            transform=transform,
            zoom=self.viewport.zoom,
            strategy=strategy,
            render_hint_enabled=render_hint_enabled,
            debug_draw_tile_grid=debug_draw_tile_grid,
            tiles_to_draw=tuple(tiles_to_draw),
            tile_size=tile_size,
            tile_overlap=tile_overlap,
            max_tile_cols=max_cols,
            max_tile_rows=max_rows,
            qpane_rect=self._qpane.rect(),
            current_pan=pan_value,
            physical_viewport_rect=physical_viewport_rect,
            visible_scene_rect=visible_scene_rect,
            visible_tile_range=visible_range,
        )

    @staticmethod
    def _uses_default_base_tile_math(
        *,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        full_image: QImage,
    ) -> bool:
        """Return True when direct old-QPane viewport math applies."""
        scene_width = max(1, round(scene.bounds.width))
        scene_height = max(1, round(scene.bounds.height))
        return (
            layer.visible
            and layer.kind == LayerKind.IMAGE
            and layer.clip is None
            and layer.placement == scene.bounds
            and not full_image.isNull()
            and full_image.width() == scene_width
            and full_image.height() == scene_height
        )

    def _calculate_default_visible_source_rect(
        self,
        *,
        source_size: QSize,
        pyramid_scale: float,
        current_pan: QPointF,
        physical_viewport_rect: QRectF,
    ) -> QRectF:
        """Compute visible source pixels for a full-scene base image."""
        safe_zoom = self.viewport.zoom if not isclose(self.viewport.zoom, 0.0) else 1.0
        safe_pyramid_scale = pyramid_scale if pyramid_scale > 0.0 else 1.0
        effective_zoom = safe_zoom / safe_pyramid_scale
        if isclose(effective_zoom, 0.0):
            effective_zoom = 1.0
        viewport_center = QPointF(physical_viewport_rect.center())
        source_center = QPointF(source_size.width() / 2.0, source_size.height() / 2.0)
        top_left = (
            physical_viewport_rect.topLeft() - viewport_center - current_pan
        ) / effective_zoom + source_center
        bottom_right = (
            physical_viewport_rect.bottomRight() - viewport_center - current_pan
        ) / effective_zoom + source_center
        source_rect = QRectF(top_left, bottom_right).normalized()
        source_bounds = QRectF(
            0.0,
            0.0,
            float(source_size.width()),
            float(source_size.height()),
        )
        return source_rect.intersected(source_bounds)

    def _resolve_default_scene(
        self,
        *,
        image_id: uuid.UUID,
        image_size: QSize,
        source_path: Path | None,
        source_revision: int,
    ) -> SceneDescriptor | None:
        """Resolve the current catalog image through the default scene provider."""
        provider = DefaultCatalogSceneProvider(
            image_id=image_id,
            image_size=image_size,
            source_path=source_path,
            revision=source_revision,
        )
        base_contribution = provider.scene_contribution()
        if base_contribution is None:
            return None
        replacements = self._scene_providers.replacement_contributions()
        if replacements:
            return SceneResolver(
                providers=tuple(_StaticSceneProvider(item) for item in replacements)
            ).resolve()
        base_scene = self._scene_providers.adapt_base_scene(
            base_contribution.scene,
            image_id,
        )
        providers = [_StaticSceneProvider(SceneContribution(base_scene, order=0))]
        providers.extend(
            _StaticSceneProvider(contribution)
            for contribution in self._scene_providers.contributions_for(
                base_scene,
                image_id,
            )
        )
        return SceneResolver(providers=tuple(providers)).resolve()

    def _build_raster_render_items(
        self, scene: SceneDescriptor, snapshot: _BaseRasterSnapshot
    ) -> tuple[RasterLayerRenderItem, ...]:
        """Build ordered raster render items from image layer descriptors."""
        items: list[RasterLayerRenderItem] = []
        visible_tile_keys: set[SceneLayerTileKey] = set()
        for layer in scene.layers:
            if not layer.visible or layer.kind != LayerKind.IMAGE:
                continue
            if self._is_snapshot_base_layer(scene, layer, snapshot):
                result = self._build_base_raster_item(scene, layer, snapshot)
            else:
                result = self._build_additional_raster_item(scene, layer, snapshot)
            visible_tile_keys.update(result.visible_tile_keys)
            item = result.item
            if item is not None:
                items.append(item)
        if hasattr(self.tile_manager, "cancel_invisible_workers"):
            self.tile_manager.cancel_invisible_workers(visible_tile_keys)
        return tuple(items)

    @staticmethod
    def _base_raster_item_from_items(
        items: tuple[RasterLayerRenderItem, ...],
    ) -> RasterLayerRenderItem | None:
        """Return the base-image raster item from ``items`` when present."""
        for item in items:
            if item.descriptor.hit_test.role in {"base-image", "placeholder-image"}:
                return item
        return None

    @staticmethod
    def _is_snapshot_base_layer(
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        snapshot: _BaseRasterSnapshot,
    ) -> bool:
        """Return True when ``layer`` is the current default base raster."""
        if scene.kind == SceneKind.PLACEHOLDER_IMAGE:
            return layer.hit_test.role == "placeholder-image"
        if not isinstance(layer.source, CatalogImageSource):
            return False
        if snapshot.image_id is None:
            return False
        return layer.layer_id == base_image_layer_id(snapshot.image_id)

    def _build_base_raster_item(
        self,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        snapshot: _BaseRasterSnapshot,
    ) -> _RasterPlanningResult:
        """Build the behavior-preserving base image render item."""
        visible_keys = self._visible_tile_keys_for_item(
            asset_key=snapshot.asset_key,
            pyramid_asset_key=snapshot.pyramid_asset_key,
            pyramid_scale=snapshot.pyramid_scale,
            visible_tile_range=snapshot.visible_tile_range,
        )
        item = RasterLayerRenderItem(
            descriptor=layer,
            source_image=snapshot.source_image,
            asset_key=snapshot.asset_key,
            pyramid_asset_key=snapshot.pyramid_asset_key,
            pyramid_scale=snapshot.pyramid_scale,
            transform=snapshot.transform,
            placement=layer.placement,
            clip=layer.clip,
            strategy=snapshot.strategy,
            render_hint_enabled=snapshot.render_hint_enabled,
            debug_draw_tile_grid=snapshot.debug_draw_tile_grid,
            tiles_to_draw=snapshot.tiles_to_draw,
            tile_size=snapshot.tile_size,
            tile_overlap=snapshot.tile_overlap,
            max_tile_cols=snapshot.max_tile_cols,
            max_tile_rows=snapshot.max_tile_rows,
            visible_tile_range=snapshot.visible_tile_range,
        )
        return _RasterPlanningResult(
            item=item, visible_tile_keys=frozenset(visible_keys)
        )

    def _build_additional_raster_item(
        self,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        snapshot: _BaseRasterSnapshot,
    ) -> _RasterPlanningResult:
        """Build a non-base image render item using the shared scene viewport."""
        full_image = self._source_image_for_layer(layer)
        if full_image is None or full_image.isNull():
            return _RasterPlanningResult(item=None, visible_tile_keys=frozenset())
        asset_key = self._render_asset_key_for_image_layer(scene, layer)
        pyramid_asset_key = self._pyramid_asset_key_for_image_layer(scene, layer)
        if pyramid_asset_key is None:
            return _RasterPlanningResult(item=None, visible_tile_keys=frozenset())
        target_width = layer.placement.width * self.viewport.zoom
        source_image = self._best_fit_image_for_layer(
            layer,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            full_image=full_image,
            target_width=target_width,
        )
        pyramid_scale = (
            source_image.width() / full_image.width() if full_image.width() > 0 else 1.0
        )
        canvas_size_physical = (
            QSizeF(layer.placement.width, layer.placement.height) * self.viewport.zoom
        )
        viewport_size_physical = snapshot.physical_viewport_rect.size()
        strategy = RenderStrategy.DIRECT
        if (
            canvas_size_physical.width() > viewport_size_physical.width()
            or canvas_size_physical.height() > viewport_size_physical.height()
        ):
            strategy = RenderStrategy.TILE
        transform = self._transform_for_placed_layer(
            scene=scene,
            layer=layer,
            source_image=source_image,
            snapshot=snapshot,
        )
        tiles_to_draw: list[TileRenderData] = []
        visible_keys: set[SceneLayerTileKey] = set()
        max_cols = 0
        max_rows = 0
        visible_range: tuple[int, int, int, int] | None = None
        if strategy == RenderStrategy.TILE:
            max_cols, max_rows = self.tile_manager.calculate_grid_dimensions(
                source_image.width(),
                source_image.height(),
            )
            visibility = visible_source_rect_for_layer(
                scene_bounds=scene.bounds,
                layer_placement=layer.placement,
                source_size=source_image.size(),
                visible_scene_rect=snapshot.visible_scene_rect,
                clip=layer.clip,
                viewport_rect=QRectF(snapshot.qpane_rect),
                item_transform=transform,
            )
            visible_range = self._calculate_tile_range_for_source_rect(
                source_rect=(
                    visibility.source_rect if visibility is not None else QRectF()
                ),
                tile_size=snapshot.tile_size,
                tile_overlap=snapshot.tile_overlap,
                max_cols=max_cols,
                max_rows=max_rows,
            )
            start_row, end_row, start_col, end_col = visible_range
            for row in range(start_row, end_row + 1):
                for col in range(start_col, end_col + 1):
                    tile_key = SceneLayerTileKey(
                        asset_key=asset_key,
                        pyramid_asset_key=pyramid_asset_key,
                        pyramid_scale=pyramid_scale,
                        row=row,
                        col=col,
                    )
                    visible_keys.add(tile_key)
                    tile_image = self.tile_manager.get_tile(tile_key, source_image)
                    if tile_image:
                        tiles_to_draw.append(
                            TileRenderData(
                                tile_image,
                                self.get_tile_draw_position(tile_key),
                            )
                        )
        item = RasterLayerRenderItem(
            descriptor=layer,
            source_image=source_image,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            pyramid_scale=pyramid_scale,
            transform=transform,
            placement=layer.placement,
            clip=layer.clip,
            strategy=strategy,
            render_hint_enabled=self._should_smooth_raster_item(
                source_image.size(),
                transform,
            ),
            debug_draw_tile_grid=snapshot.debug_draw_tile_grid,
            tiles_to_draw=tuple(tiles_to_draw),
            tile_size=snapshot.tile_size,
            tile_overlap=snapshot.tile_overlap,
            max_tile_cols=max_cols,
            max_tile_rows=max_rows,
            visible_tile_range=visible_range,
        )
        return _RasterPlanningResult(
            item=item, visible_tile_keys=frozenset(visible_keys)
        )

    @staticmethod
    def _visible_tile_keys_for_item(
        *,
        asset_key: SceneLayerAssetKey,
        pyramid_asset_key: SceneLayerAssetKey,
        pyramid_scale: float,
        visible_tile_range: tuple[int, int, int, int] | None,
    ) -> set[SceneLayerTileKey]:
        """Return visible tile keys for an already-planned tiled raster item."""
        if visible_tile_range is None:
            return set()
        start_row, end_row, start_col, end_col = visible_tile_range
        return {
            SceneLayerTileKey(
                asset_key=asset_key,
                pyramid_asset_key=pyramid_asset_key,
                pyramid_scale=pyramid_scale,
                row=row,
                col=col,
            )
            for row in range(start_row, end_row + 1)
            for col in range(start_col, end_col + 1)
        }

    @staticmethod
    def _should_smooth_raster_item(
        source_size: QSize,
        transform: QTransform,
    ) -> bool:
        """Return True when raster scaling should use filtered interpolation."""
        source_width = float(source_size.width())
        source_height = float(source_size.height())
        if source_width <= 0.0 or source_height <= 0.0:
            return False
        source_rect = QRectF(0.0, 0.0, source_width, source_height)
        panel_rect = transform.mapRect(source_rect)
        if panel_rect.isEmpty():
            return False
        scale_x = abs(panel_rect.width() / source_width)
        scale_y = abs(panel_rect.height() / source_height)
        effective_scale = max(scale_x, scale_y)
        return effective_scale < 2.0

    def _transform_for_placed_layer(
        self,
        *,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        source_image: QImage,
        snapshot: _BaseRasterSnapshot,
    ) -> QTransform:
        """Map source pixels into the layer placement before viewport transform."""
        scene_size = QSize(
            max(1, round(scene.bounds.width)),
            max(1, round(scene.bounds.height)),
        )
        transform = self.viewport.get_transform(
            scene_size,
            1.0,
            pan_override=snapshot.current_pan,
            content_snapshot=snapshot.content_snapshot,
        )
        transform.translate(
            layer.placement.x - scene.bounds.x,
            layer.placement.y - scene.bounds.y,
        )
        transform.scale(
            layer.placement.width / source_image.width(),
            layer.placement.height / source_image.height(),
        )
        return transform

    def _transform_for_placed_size(
        self,
        *,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        source_size: QSize,
        snapshot: _BaseRasterSnapshot,
    ) -> QTransform:
        """Map a source size into the layer placement before viewport transform."""
        return self._transform_for_placed_geometry(
            scene=scene,
            layer=layer,
            source_size=source_size,
            content_snapshot=snapshot.content_snapshot,
            current_pan=snapshot.current_pan,
        )

    def _transform_for_placed_geometry(
        self,
        *,
        scene: SceneDescriptor,
        layer: LayerDescriptor,
        source_size: QSize,
        content_snapshot: SceneContentSnapshot,
        current_pan: QPointF,
    ) -> QTransform:
        """Map a source size into layer placement for geometry-only queries."""
        scene_size = QSize(
            max(1, round(scene.bounds.width)),
            max(1, round(scene.bounds.height)),
        )
        transform = self.viewport.get_transform(
            scene_size,
            1.0,
            pan_override=current_pan,
            content_snapshot=content_snapshot,
        )
        transform.translate(
            layer.placement.x - scene.bounds.x,
            layer.placement.y - scene.bounds.y,
        )
        transform.scale(
            layer.placement.width / source_size.width(),
            layer.placement.height / source_size.height(),
        )
        return transform

    def _hit_test_render_item(
        self,
        plan: SceneRenderPlan,
        item: SceneRenderItem,
        panel_point: QPointF,
    ) -> SceneLayerHitTestResult | None:
        """Return hit metadata when ``panel_point`` intersects ``item``."""
        descriptor = item.descriptor
        if (
            not descriptor.visible
            or not descriptor.hit_test.enabled
            or descriptor.source is None
        ):
            return None
        inverse, invertible = item.transform.inverted()
        if not invertible:
            return None
        source_point = inverse.map(panel_point)
        source_width, source_height = self._render_item_source_size(item)
        source_rect = QRectF(0.0, 0.0, float(source_width), float(source_height))
        if not source_rect.contains(source_point):
            return None
        if not self._source_point_inside_clip(plan, item, source_point):
            return None
        placement = item.placement
        scene_point = QPointF(
            placement.x + (source_point.x() * placement.width / source_width),
            placement.y + (source_point.y() * placement.height / source_height),
        )
        return SceneLayerHitTestResult(
            scene_id=descriptor.scene_id,
            layer_id=descriptor.layer_id,
            role=descriptor.hit_test.role,
            source=descriptor.source,
            panel_point=QPointF(panel_point),
            scene_point=scene_point,
            source_point=source_point,
            selectable=descriptor.hit_test.selectable,
        )

    def _source_point_inside_clip(
        self,
        plan: SceneRenderPlan,
        item: SceneRenderItem,
        source_point: QPointF,
    ) -> bool:
        """Return True when ``source_point`` is inside the item's layer clip."""
        clip = item.clip
        if clip is None:
            return True
        source_width, source_height = self._render_item_source_size(item)
        if clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_SCENE:
            scene_clip = QRectF(
                plan.scene_bounds.x + clip.x * plan.scene_bounds.width,
                plan.scene_bounds.y + clip.y * plan.scene_bounds.height,
                clip.width * plan.scene_bounds.width,
                clip.height * plan.scene_bounds.height,
            )
            source_clip = self._scene_clip_to_source_rect(
                item,
                scene_clip,
                source_width=source_width,
                source_height=source_height,
            )
        elif clip.coordinate_space == ClipCoordinateSpace.SCENE:
            source_clip = self._scene_clip_to_source_rect(
                item,
                QRectF(clip.x, clip.y, clip.width, clip.height),
                source_width=source_width,
                source_height=source_height,
            )
        elif clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_VIEWPORT:
            source_clip = self._viewport_clip_to_source_rect(
                item,
                QRectF(
                    plan.qpane_rect.x() + clip.x * plan.qpane_rect.width(),
                    plan.qpane_rect.y() + clip.y * plan.qpane_rect.height(),
                    clip.width * plan.qpane_rect.width(),
                    clip.height * plan.qpane_rect.height(),
                ),
            )
        elif clip.coordinate_space == ClipCoordinateSpace.VIEWPORT:
            source_clip = self._viewport_clip_to_source_rect(
                item,
                QRectF(clip.x, clip.y, clip.width, clip.height),
            )
        else:
            return True
        return source_clip.contains(source_point)

    @staticmethod
    def _render_item_source_size(item: SceneRenderItem) -> tuple[int, int]:
        """Return render-item source dimensions for hit testing."""
        if isinstance(item, RasterLayerRenderItem):
            return item.source_image.width(), item.source_image.height()
        return item.pixmap.width(), item.pixmap.height()

    @staticmethod
    def _scene_clip_to_source_rect(
        item: SceneRenderItem,
        scene_clip: QRectF,
        *,
        source_width: int,
        source_height: int,
    ) -> QRectF:
        """Convert a scene-space clip into source coordinates."""
        placement = item.placement
        if placement.width <= 0.0 or placement.height <= 0.0:
            return QRectF()
        return QRectF(
            (scene_clip.x() - placement.x) * source_width / placement.width,
            (scene_clip.y() - placement.y) * source_height / placement.height,
            scene_clip.width() * source_width / placement.width,
            scene_clip.height() * source_height / placement.height,
        )

    @staticmethod
    def _viewport_clip_to_source_rect(
        item: SceneRenderItem,
        viewport_clip: QRectF,
    ) -> QRectF:
        """Convert a viewport-space clip into source coordinates."""
        inverse, invertible = item.transform.inverted()
        if not invertible:
            return QRectF()
        return inverse.mapRect(viewport_clip)

    def _build_mask_render_items(
        self,
        scene: SceneDescriptor,
        snapshot: _BaseRasterSnapshot,
        base_item: RasterLayerRenderItem | None,
    ) -> tuple[MaskLayerRenderItem, ...]:
        """Build mask render items from resolved mask layer descriptors."""
        service = self._mask_service()
        if service is None or base_item is None or snapshot.base_image.width() <= 0:
            return ()
        scale = snapshot.source_image.width() / snapshot.base_image.width()
        if scale <= 0:
            scale = 1.0
        items: list[MaskLayerRenderItem] = []
        for layer in scene.layers:
            if (
                not layer.visible
                or layer.kind != LayerKind.MASK
                or not isinstance(layer.source, MaskLayerSource)
            ):
                continue
            pixmap = service.getColorizedMaskById(layer.source.mask_id, scale=scale)
            if pixmap is None or pixmap.isNull():
                continue
            items.append(
                MaskLayerRenderItem(
                    descriptor=layer,
                    pixmap=pixmap,
                    asset_key=mask_layer_asset_key(
                        scene_id=scene.scene_id,
                        mask_id=layer.source.mask_id,
                        revision=layer.source_revision,
                    ),
                    transform=base_item.transform,
                    placement=layer.placement,
                    clip=layer.clip,
                    render_hint_enabled=base_item.render_hint_enabled,
                    scale=scale,
                )
            )
        return tuple(items)

    def _render_asset_key_for_image_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneLayerAssetKey:
        """Return the render/tile cache identity for a resolved image layer."""
        if isinstance(layer.source, CatalogImageSource):
            if layer.layer_id == base_image_layer_id(layer.source.image_id):
                return default_catalog_asset_key(
                    layer.source.image_id,
                    revision=layer.source_revision,
                    source_path=layer.source.source_path,
                )
            return scene_image_asset_key(
                scene_id=scene.scene_id,
                layer_id=layer.layer_id,
                source_id=layer.source.image_id,
                source_kind="catalog-image",
                revision=layer.source_revision,
                source_path=layer.source.source_path,
            )
        if scene.kind == SceneKind.PLACEHOLDER_IMAGE and isinstance(
            layer.source, PlaceholderImageSource
        ):
            return SceneLayerAssetKey(
                scene_id=layer.scene_id,
                layer_id=layer.layer_id,
                source_id=layer.source.source_id,
                source_kind="placeholder-image",
                source_revision=layer.source_revision,
                source_path=self._qpane.currentImagePath,
            )
        raise TypeError("raster image render items require an image source")

    def _pyramid_asset_key_for_image_layer(
        self, scene: SceneDescriptor, layer: LayerDescriptor
    ) -> SceneLayerAssetKey | None:
        """Return the source/pyramid identity for a resolved image layer."""
        if isinstance(layer.source, CatalogImageSource):
            return self._catalog.defaultAssetKeyForImage(layer.source.image_id)
        if scene.kind == SceneKind.PLACEHOLDER_IMAGE and isinstance(
            layer.source, PlaceholderImageSource
        ):
            return self._render_asset_key_for_image_layer(scene, layer)
        raise TypeError("raster image render items require an image source")

    def _source_image_for_layer(self, layer: LayerDescriptor) -> QImage | None:
        """Return source pixels for an image layer descriptor."""
        return self._source_resolvers.source_image(layer.source)

    def _best_fit_image_for_layer(
        self,
        layer: LayerDescriptor,
        *,
        asset_key: SceneLayerAssetKey,
        pyramid_asset_key: SceneLayerAssetKey,
        full_image: QImage,
        target_width: float,
    ) -> QImage:
        """Return the best available source image for a raster layer."""
        return self._source_resolvers.best_fit_image(
            layer.source,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
            full_image=full_image,
            target_width=target_width,
        )

    def _resolve_active_scene_content(self) -> _ActiveSceneContent | None:
        """Resolve catalog or placeholder content without reading widget mirror state."""
        replacement_content = self._resolve_replacement_scene_content()
        if replacement_content is not None:
            return replacement_content
        current_id = self._catalog.getCurrentId()
        current_image = self._catalog.getCurrentImage()
        if (
            current_id is not None
            and current_image is not None
            and not current_image.isNull()
        ):
            source_path = self._catalog.getCurrentPath()
            source_revision = self._catalog_revision(current_id)
            scene = self._resolve_default_scene(
                image_id=current_id,
                image_size=current_image.size(),
                source_path=source_path,
                source_revision=source_revision,
            )
            if scene is None:
                return None
            layer = self._first_image_layer(scene)
            if layer is None:
                return None
            asset_key = self._render_asset_key_for_image_layer(scene, layer)
            pyramid_asset_key = self._pyramid_asset_key_for_image_layer(scene, layer)
            if pyramid_asset_key is None:
                return None
            return _ActiveSceneContent(
                scene=scene,
                base_image=current_image,
                image_id=current_id,
                source_path=source_path,
                source_revision=source_revision,
                asset_key=asset_key,
                pyramid_asset_key=pyramid_asset_key,
            )
        placeholder = self._placeholder_content()
        if placeholder is None:
            return None
        placeholder_image = getattr(placeholder, "image", None)
        if placeholder_image is None or placeholder_image.isNull():
            return None
        source_path = getattr(placeholder, "source_path", None)
        source_revision = max(0, int(getattr(placeholder, "revision", 0) or 0))
        scene = build_placeholder_scene(
            image_size=placeholder_image.size(),
            source_path=source_path,
            revision=source_revision,
        )
        layer = self._first_image_layer(scene)
        if layer is None:
            return None
        asset_key = self._render_asset_key_for_image_layer(scene, layer)
        return _ActiveSceneContent(
            scene=scene,
            base_image=placeholder_image,
            image_id=None,
            source_path=source_path,
            source_revision=source_revision,
            asset_key=asset_key,
            pyramid_asset_key=asset_key,
        )

    def _resolve_replacement_scene_content(self) -> _ActiveSceneContent | None:
        """Resolve an active replacement scene without requiring catalog selection."""
        replacements = self._scene_providers.replacement_contributions()
        if not replacements:
            return None
        scene = SceneResolver(
            providers=tuple(_StaticSceneProvider(item) for item in replacements)
        ).resolve()
        if scene is None:
            return None
        layer = self._first_image_layer(scene)
        if layer is None:
            return None
        source_image = self._source_image_for_layer(layer)
        if source_image is None or source_image.isNull():
            return None
        asset_key = self._render_asset_key_for_image_layer(scene, layer)
        pyramid_asset_key = self._pyramid_asset_key_for_image_layer(scene, layer)
        if pyramid_asset_key is None:
            return None
        image_id = (
            layer.source.image_id
            if isinstance(layer.source, CatalogImageSource)
            else None
        )
        source_path = self._source_resolvers.source_path(layer.source)
        return _ActiveSceneContent(
            scene=scene,
            base_image=source_image,
            image_id=image_id,
            source_path=source_path,
            source_revision=layer.source_revision,
            asset_key=asset_key,
            pyramid_asset_key=pyramid_asset_key,
        )

    def _cached_active_scene_content(self) -> _ActiveSceneContent | None:
        """Return resolved active content, reusing it while scene identity is stable."""
        cache_key = self._active_content_cache_key()
        if (
            cache_key == self._cached_content_key
            and self._cached_active_content is not None
        ):
            return self._cached_active_content
        active_content = self._resolve_active_scene_content()
        self._cached_content_key = cache_key
        self._cached_active_content = active_content
        self._cached_content_snapshot = (
            self._content_snapshot_for_active_content(active_content)
            if active_content is not None
            else None
        )
        return active_content

    def _active_content_cache_key(self) -> tuple[object, ...]:
        """Return revision values that affect active scene/content geometry."""
        current_id = self._catalog.getCurrentId()
        source_path = self._catalog.getCurrentPath() if current_id is not None else None
        source_revision = self._catalog_revision(current_id)
        composition_id = None
        composition_revision = None
        comparison_state = None
        comparison_source_revision = 0
        service_getter = getattr(self._qpane, "compositionService", None)
        if callable(service_getter):
            try:
                service = service_getter()
            except Exception:  # pragma: no cover - defensive teardown guard
                service = None
            if service is not None:
                composition_id = service.current_composition_id()
                revision_getter = getattr(service, "revision", None)
                if callable(revision_getter):
                    composition_revision = revision_getter()
        compare_service = getattr(self._qpane, "compare_service", None)
        if compare_service is not None:
            try:
                state = compare_service.state()
            except Exception:  # pragma: no cover - defensive teardown guard
                state = None
            if state is not None:
                comparison_state = (
                    state.enabled,
                    state.source_id,
                    state.source_kind,
                    state.split_position,
                    state.orientation,
                )
                revision_getter = getattr(compare_service, "source_revision", None)
                if callable(revision_getter):
                    comparison_source_revision = revision_getter()
        placeholder_revision = 0
        placeholder = self._placeholder_content()
        if placeholder is not None:
            placeholder_revision = max(
                0,
                int(getattr(placeholder, "revision", 0) or 0),
            )
        return (
            current_id,
            source_path,
            source_revision,
            composition_id,
            composition_revision,
            comparison_state,
            comparison_source_revision,
            placeholder_revision,
            self._scene_provider_revision(),
        )

    def _scene_provider_revision(self) -> object:
        """Return a best-effort revision identity for scene provider inputs."""
        revision_getter = getattr(self._scene_providers, "revision", None)
        if callable(revision_getter):
            return revision_getter()
        return id(self._scene_providers)

    def _content_snapshot_for_active_content(
        self, active_content: _ActiveSceneContent
    ) -> SceneContentSnapshot:
        """Project resolved active content into geometry consumed by view helpers."""
        base_image_size = active_content.base_image.size()
        scene_size = QSize(
            max(1, round(active_content.scene.bounds.width)),
            max(1, round(active_content.scene.bounds.height)),
        )
        if (
            active_content.scene.kind == SceneKind.EXPLICIT
            or base_image_size != scene_size
        ):
            base_image_size = QSize(
                scene_size,
            )
        return SceneContentSnapshot(
            scene_id=active_content.scene.scene_id,
            base_asset_key=active_content.asset_key,
            base_image_size=base_image_size,
            scene_bounds=active_content.scene.bounds,
            active_content_bounds=active_content.scene.bounds,
            current_path=active_content.source_path,
        )

    def _placeholder_content(self) -> object | None:
        """Return catalog-owned placeholder content when a provider is installed."""
        provider = self._placeholder_content_provider
        return provider() if provider is not None else None

    def _mask_service(self) -> object | None:
        """Return the active mask service when installed."""
        service = getattr(self._qpane, "mask_service", None)
        return service

    @staticmethod
    def _first_image_layer(scene: SceneDescriptor) -> LayerDescriptor | None:
        """Return the first visible image layer in scene order."""
        return next(
            (
                candidate
                for candidate in scene.layers
                if candidate.visible and candidate.kind == LayerKind.IMAGE
            ),
            None,
        )

    @staticmethod
    def _hit_test_items_for_scene(
        scene: SceneDescriptor,
    ) -> tuple[SceneHitTestItem, ...]:
        """Project layer hit-test descriptors into render-plan metadata."""
        return tuple(
            SceneHitTestItem(
                scene_id=scene.scene_id,
                layer_id=layer.layer_id,
                bounds=layer.placement,
                enabled=layer.hit_test.enabled,
                selectable=layer.hit_test.selectable,
                role=layer.hit_test.role,
                source=layer.source,
            )
            for layer in scene.layers
            if layer.hit_test.enabled
        )

    def _cached_hit_test_items_for_scene(
        self,
        scene: SceneDescriptor,
    ) -> tuple[SceneHitTestItem, ...]:
        """Return cached hit-test metadata while scene hit-test inputs are stable."""
        cache_key = self._hit_test_cache_key(scene)
        if cache_key == self._cached_hit_test_key:
            return self._cached_hit_test_items
        items = self._hit_test_items_for_scene(scene)
        self._cached_hit_test_key = cache_key
        self._cached_hit_test_items = items
        return items

    @staticmethod
    def _hit_test_cache_key(scene: SceneDescriptor) -> tuple[object, ...]:
        """Return stable scene fields that affect hit-test metadata projection."""
        return (
            scene.scene_id,
            tuple(
                (
                    layer.layer_id,
                    layer.placement,
                    layer.hit_test.enabled,
                    layer.hit_test.selectable,
                    layer.hit_test.role,
                    layer.source,
                    layer.source_revision,
                )
                for layer in scene.layers
            ),
        )

    def _catalog_revision(self, image_id: uuid.UUID | None) -> int:
        """Return the catalog revision for ``image_id`` when available."""
        if image_id is None:
            return 0
        revision_getter = getattr(self._catalog, "getRevision", None)
        if not callable(revision_getter):
            return 0
        revision = revision_getter(image_id)
        return max(0, int(revision or 0))
