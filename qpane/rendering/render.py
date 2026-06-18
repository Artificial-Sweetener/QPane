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

"""Rendering pipeline and metrics helpers for the QPane viewer."""

import time

from dataclasses import dataclass
from math import isclose

from typing import TYPE_CHECKING


from PySide6.QtCore import QPointF, QRect, QRectF, QSize, QSizeF, Qt

from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRegion


from .coordinates import CoordinateContext
from ..scene.render_plan import (
    MaskLayerRenderItem,
    RasterLayerRenderItem,
    RenderStrategy,
    SceneRenderPlan,
)
from ..scene.model import ClipCoordinateSpace


if TYPE_CHECKING:
    from ..qpane import QPane


@dataclass(frozen=True)
class RendererMetrics:
    """Runtime counters describing renderer buffer reuse behaviour."""

    base_buffer_allocations: int
    scroll_attempts: int
    scroll_hits: int
    scroll_misses: int
    full_redraws: int
    partial_redraws: int
    last_paint_ms: float


class Renderer:
    """Own the offscreen buffers plus reuse heuristics for the QPane widget."""

    _BUFFER_OVERSCAN_PHYSICAL_PX = 2

    def __init__(self, qpane: "QPane"):
        """Bind rendering to `qpane` while tracking buffer reuse health."""
        self._qpane = qpane
        self._current_render_plan: SceneRenderPlan | None = None
        self._base_image_buffer = None
        self._dirty_region = QRegion()
        self._buffer_pan = QPointF(0, 0)
        self._subpixel_pan_offset = QPointF(0, 0)
        self._viewport_physical_size = QSize()
        self._scroll_temp: QImage | None = None
        self._last_paint_duration_ms = 0.0
        self._paint_duration_sum_ms = 0.0
        self._paint_duration_count = 0
        self._paint_duration_max_ms = 0.0
        self._base_buffer_allocations = 0
        self._scroll_attempts = 0
        self._scroll_hits = 0
        self._scroll_misses = 0
        self._full_redraws = 0
        self._partial_redraws = 0

    @property
    def qpane(self) -> "QPane":
        """Return the QPane associated with this renderer."""
        return self._qpane

    def paint(self, plan: SceneRenderPlan):
        """Prepare offscreen buffers for the requested scene without drawing to the widget."""
        start_time = time.perf_counter()
        self._current_render_plan = plan
        # Ensure buffers are allocated. The QPane is responsible for calling
        # _allocate_buffers on resize, but we need to handle the initial case.
        if self._base_image_buffer is None:
            self.markDirty(plan.qpane_rect)  # Mark entire view dirty for first paint
        # Redraw dirty buffers if any region has been marked as dirty.
        if not self._dirty_region.isEmpty():
            # Pass the entire region object and plan to the redraw methods.
            self._redraw_base_image_buffer(self._dirty_region, plan)
        # Clear the dirty region now that buffer painting is complete for this frame.
        self._dirty_region = QRegion()
        end_time = time.perf_counter()
        self._last_paint_duration_ms = (end_time - start_time) * 1000
        if self._last_paint_duration_ms > 0.0:
            self._paint_duration_sum_ms += self._last_paint_duration_ms
            self._paint_duration_count += 1
            self._paint_duration_max_ms = max(
                self._paint_duration_max_ms, self._last_paint_duration_ms
            )
        self._mark_diagnostics_dirty()

    def allocate_buffers(self, physical_size: QSize, dpr: float):
        """Allocate and clear the base buffer sized to the physical viewport."""
        self._viewport_physical_size = QSize(physical_size)
        self._base_image_buffer = self._allocate_dpi_buffer(
            self._overscanned_buffer_size(physical_size),
            dpr,
        )
        self._base_image_buffer.fill(Qt.transparent)
        self._scroll_temp = None
        self._base_buffer_allocations += 1
        # Mark the entire view as dirty since the buffers are new.
        self.markDirty()

    def buffer_matches_viewport(self, physical_size: QSize, dpr: float) -> bool:
        """Return True when the allocated backing store matches the viewport."""
        if self._base_image_buffer is None:
            return False
        return (
            self._viewport_physical_size == physical_size
            and self._base_image_buffer.devicePixelRatio() == dpr
            and self._base_image_buffer.size()
            == self._overscanned_buffer_size(physical_size)
        )

    def markDirty(self, dirty_rect: QRect | QRectF | QRegion | None = None):
        """Mark a region dirty for the next render pass; None targets the full viewport."""
        if dirty_rect is None:
            self._dirty_region += QRect(-100000, -100000, 200000, 200000)
            return
        if isinstance(dirty_rect, QRegion):
            if not dirty_rect.isEmpty():
                self._dirty_region += QRegion(dirty_rect)
            return
        if isinstance(dirty_rect, QRectF):
            dirty_rect = dirty_rect.toAlignedRect()
        if isinstance(dirty_rect, QRect):
            if dirty_rect.isEmpty():
                return
            self._dirty_region += dirty_rect
            return
        raise TypeError(f"Unsupported dirty input: {type(dirty_rect)!r}")

    def tryScrollBuffers(self, new_pan: QPointF) -> bool:
        """Attempts to reuse the existing buffer by scrolling and repairing edge strips."""
        if self._base_image_buffer is None:
            return False
        delta_pan = new_pan - self._buffer_pan
        self._scroll_attempts += 1
        dx = int(delta_pan.x())
        dy = int(delta_pan.y())
        if dx == 0 and dy == 0:
            self._scroll_hits += 1
            viewport = self.qpane.view().viewport
            self._subpixel_pan_offset = viewport.pan - self._buffer_pan
            self.qpane.update()
            return True
        if (
            abs(dx) >= self._base_image_buffer.width()
            or abs(dy) >= self._base_image_buffer.height()
        ):
            self._scroll_misses += 1
            return False
        context = CoordinateContext(self.qpane)
        logical_delta = context.physical_to_logical(QPointF(dx, dy))
        base_image = self._base_image_buffer
        previous_buffer_pan = QPointF(self._buffer_pan)
        previous_subpixel_offset = QPointF(self._subpixel_pan_offset)
        if (
            self._scroll_temp is None
            or self._scroll_temp.size() != base_image.size()
            or self._scroll_temp.devicePixelRatio() != base_image.devicePixelRatio()
        ):
            self._scroll_temp = self._allocate_dpi_buffer(
                base_image.size(), base_image.devicePixelRatio()
            )
        self._scroll_temp.swap(self._base_image_buffer)
        self._base_image_buffer.fill(Qt.transparent)
        painter = QPainter(self._base_image_buffer)
        painter.drawImage(logical_delta, self._scroll_temp)
        painter.end()
        self._mark_diagnostics_dirty()
        self._buffer_pan += QPointF(dx, dy)
        w = self._base_image_buffer.width()
        h = self._base_image_buffer.height()
        repair_rects: list[QRect] = []
        if dy > 0:
            repair_rects.append(QRect(0, 0, w, dy))
        if dy < 0:
            repair_rects.append(QRect(0, h + dy, w, -dy))
        if dx > 0:
            repair_rects.append(QRect(0, 0, dx, h))
        if dx < 0:
            repair_rects.append(QRect(w + dx, 0, -dx, h))
        if repair_rects:
            qpane_view = getattr(self.qpane, "view", None)
            view = qpane_view() if callable(qpane_view) else None
            plan_calculate = getattr(view, "calculateRenderPlan", None)
            if not callable(plan_calculate):
                raise AttributeError(
                    "QPane view must provide calculateRenderPlan for buffer repair"
                )
            plan = plan_calculate(use_pan=self._buffer_pan)
            if plan and self._repair_base_buffer_strips(repair_rects, plan) is False:
                self._scroll_temp.swap(self._base_image_buffer)
                self._buffer_pan = previous_buffer_pan
                self._subpixel_pan_offset = previous_subpixel_offset
                self._scroll_misses += 1
                return False
        self._scroll_hits += 1
        viewport = self.qpane.view().viewport
        self._subpixel_pan_offset = viewport.pan - self._buffer_pan
        self.qpane.update()
        self._mark_diagnostics_dirty()
        return True

    def get_last_paint_duration_ms(self) -> float:
        """Return the duration of the last paint call in milliseconds."""
        return self._last_paint_duration_ms

    def get_current_render_plan(self) -> SceneRenderPlan | None:
        """Return the most recent scene render plan captured during painting."""
        return self._current_render_plan

    def get_base_buffer(self) -> QImage | None:
        """Return the current base image buffer used for painting."""
        return self._base_image_buffer

    def get_subpixel_pan_offset(self) -> QPointF:
        """Return the subpixel offset applied when scrolling reused buffers."""
        return self._subpixel_pan_offset

    def draw_base_buffer(self, painter: QPainter) -> None:
        """Draw the viewport crop from the overscanned base buffer."""
        if self._base_image_buffer is None or self._viewport_physical_size.isEmpty():
            return
        margin = self._BUFFER_OVERSCAN_PHYSICAL_PX
        source_rect = QRectF(
            margin - self._subpixel_pan_offset.x(),
            margin - self._subpixel_pan_offset.y(),
            float(self._viewport_physical_size.width()),
            float(self._viewport_physical_size.height()),
        )
        painter.drawImage(
            QRectF(self.qpane.rect()), self._base_image_buffer, source_rect
        )

    def snapshot_metrics(self) -> RendererMetrics:
        """Return current renderer reuse counters for diagnostics displays."""
        return RendererMetrics(
            base_buffer_allocations=self._base_buffer_allocations,
            scroll_attempts=self._scroll_attempts,
            scroll_hits=self._scroll_hits,
            scroll_misses=self._scroll_misses,
            full_redraws=self._full_redraws,
            partial_redraws=self._partial_redraws,
            last_paint_ms=self._last_paint_duration_ms,
        )

    def paint_stats(self) -> tuple[float, float, float]:
        """Return (last, average, max) paint durations in milliseconds."""
        average = (
            self._paint_duration_sum_ms / self._paint_duration_count
            if self._paint_duration_count > 0
            else 0.0
        )
        return (
            self._last_paint_duration_ms,
            average,
            self._paint_duration_max_ms,
        )

    def _buffer_rect_to_image_rect(
        self, buffer_rect_phys: QRectF, item: RasterLayerRenderItem
    ) -> QRectF:
        """Map a physical buffer rectangle back into source-image coordinates using the inverse transform."""
        # The item transform maps source image coordinates to logical buffer coordinates.
        # We need the inverse to map from the buffer back to the source image.
        fwd_transform = item.transform
        inv_transform, is_invertible = fwd_transform.inverted()
        if not is_invertible:
            return QRectF()
        context = CoordinateContext(self.qpane)
        # Map PHYSICAL buffer coordinates -> LOGICAL qpane space before projecting
        # through the inverted transform into SOURCE image space.
        buffer_rect_log = self._buffer_physical_to_widget_logical(
            buffer_rect_phys,
            context,
        )
        # Map the entire logical buffer rect back to the source image space at once.
        # This is more numerically stable than mapping individual points.
        return inv_transform.mapRect(buffer_rect_log)

    def _repair_base_buffer_strips(
        self, repair_rects: list[QRect], plan: SceneRenderPlan
    ) -> bool:
        """Repair scene strips after buffer scrolling."""
        if not plan.render_items:
            return True
        if self._can_repair_base_strips_directly(plan):
            self._repair_base_raster_strips_directly(repair_rects, plan)
            return True
        if self._repair_layered_strips(repair_rects, plan):
            return True
        painter = QPainter(self._base_image_buffer)
        context = CoordinateContext(self.qpane)
        try:
            self._translate_to_widget_origin(painter, context)
            clip_region = QRegion()
            for rect in repair_rects:
                logical_rect = self._buffer_physical_to_widget_logical(
                    QRectF(rect),
                    context,
                ).toAlignedRect()
                clip_region = clip_region.united(QRegion(logical_rect))
            painter.setClipRegion(clip_region)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            for rect in repair_rects:
                painter.fillRect(
                    self._buffer_physical_to_widget_logical(QRectF(rect), context),
                    Qt.transparent,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            self._draw_visible_scene_items(painter, plan)
        finally:
            painter.end()
        return True

    def _can_repair_base_strips_directly(self, plan: SceneRenderPlan) -> bool:
        """Return True when scroll repair can use the base-image fast path."""
        base_item = self._base_only_raster_item(plan)
        return base_item is not None and base_item.strategy == RenderStrategy.DIRECT

    def _repair_base_raster_strips_directly(
        self, repair_rects: list[QRect], plan: SceneRenderPlan
    ) -> None:
        """Repair base-only scroll strips by drawing mapped source rectangles."""
        base_item = plan.base_raster_item
        if base_item is None:
            return
        painter = QPainter(self._base_image_buffer)
        context = CoordinateContext(self.qpane)
        try:
            self._translate_to_widget_origin(painter, context)
            clip_region = self._logical_region_for_physical_rects(
                repair_rects,
                context,
            )
            painter.setClipRegion(clip_region)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            for rect in repair_rects:
                painter.fillRect(
                    self._buffer_physical_to_widget_logical(QRectF(rect), context),
                    Qt.transparent,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.save()
            try:
                if base_item.render_hint_enabled:
                    painter.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform,
                        True,
                    )
                painter.setTransform(base_item.transform, True)
                self._draw_raster_source_strips(
                    painter,
                    base_item,
                    repair_rects,
                    context,
                    draw_source_for_tiled=True,
                )
                if base_item.debug_draw_tile_grid:
                    self._draw_tile_debug_overlay(painter, plan, base_item)
            finally:
                painter.restore()
        finally:
            painter.end()

    def _repair_layered_strips(
        self,
        repair_rects: list[QRect],
        plan: SceneRenderPlan,
    ) -> bool:
        """Repair layered scroll strips while culling items outside the strip."""
        context = CoordinateContext(self.qpane)
        repair_region = self._logical_region_for_physical_rects(repair_rects, context)
        if repair_region.isEmpty():
            return True
        painter = QPainter(self._base_image_buffer)
        try:
            self._translate_to_widget_origin(painter, context)
            painter.setClipRegion(repair_region)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            for rect in repair_rects:
                painter.fillRect(
                    self._buffer_physical_to_widget_logical(QRectF(rect), context),
                    Qt.transparent,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            for item in plan.render_items:
                if not item.descriptor.visible:
                    continue
                item_bounds = self._item_panel_bounds(item)
                if item_bounds.isEmpty():
                    return False
                if repair_region.intersected(QRegion(item_bounds)).isEmpty():
                    continue
                painter.save()
                try:
                    painter.setTransform(item.transform, True)
                    self._apply_layer_clip(painter, plan, item)
                    if isinstance(item, RasterLayerRenderItem):
                        if not self._draw_raster_source_strips(
                            painter,
                            item,
                            repair_rects,
                            context,
                        ):
                            return False
                    elif isinstance(item, MaskLayerRenderItem):
                        painter.setOpacity(item.descriptor.opacity)
                        if not self._draw_pixmap_source_strips(
                            painter,
                            item,
                            repair_rects,
                            context,
                        ):
                            painter.drawPixmap(0, 0, item.pixmap)
                finally:
                    painter.restore()
            return True
        finally:
            painter.end()

    def _logical_region_for_physical_rects(
        self,
        repair_rects: list[QRect],
        context: CoordinateContext,
    ) -> QRegion:
        """Return a logical clip region for physical buffer repair rectangles."""
        clip_region = QRegion()
        for rect in repair_rects:
            logical_rect = self._buffer_physical_to_widget_logical(
                QRectF(rect),
                context,
            ).toAlignedRect()
            clip_region = clip_region.united(QRegion(logical_rect))
        return clip_region

    @staticmethod
    def _item_panel_bounds(
        item: RasterLayerRenderItem | MaskLayerRenderItem,
    ) -> QRect:
        """Return the approximate panel bounds for a render item."""
        source_width, source_height = Renderer._item_source_size(item)
        if source_width <= 0 or source_height <= 0:
            return QRect()
        source_rect = QRectF(0.0, 0.0, float(source_width), float(source_height))
        return (
            item.transform.mapRect(source_rect)
            .toAlignedRect()
            .adjusted(
                -1,
                -1,
                1,
                1,
            )
        )

    def _draw_raster_source_strips(
        self,
        painter: QPainter,
        item: RasterLayerRenderItem,
        repair_rects: list[QRect],
        context: CoordinateContext,
        *,
        draw_source_for_tiled: bool = False,
    ) -> bool:
        """Draw raster pixels that map into the repaired strip rectangles."""
        inverse, invertible = item.transform.inverted()
        if not invertible:
            return False
        source_bounds = QRectF(
            0.0,
            0.0,
            float(item.source_image.width()),
            float(item.source_image.height()),
        )
        for rect in repair_rects:
            source_rect = inverse.mapRect(
                self._buffer_physical_to_widget_logical(QRectF(rect), context)
            ).intersected(source_bounds)
            if source_rect.isEmpty():
                continue
            if item.strategy == RenderStrategy.TILE and not draw_source_for_tiled:
                painter.drawImage(source_rect, item.source_image, source_rect)
                self._draw_intersecting_tile_strips(painter, item, source_rect)
            else:
                painter.drawImage(source_rect, item.source_image, source_rect)
        return True

    def _draw_pixmap_source_strips(
        self,
        painter: QPainter,
        item: MaskLayerRenderItem,
        repair_rects: list[QRect],
        context: CoordinateContext,
    ) -> bool:
        """Draw only pixmap source pixels that map into repaired strips."""
        inverse, invertible = item.transform.inverted()
        if not invertible:
            return False
        source_bounds = QRectF(
            0.0,
            0.0,
            float(item.pixmap.width()),
            float(item.pixmap.height()),
        )
        for rect in repair_rects:
            source_rect = inverse.mapRect(
                self._buffer_physical_to_widget_logical(QRectF(rect), context)
            ).intersected(source_bounds)
            if source_rect.isEmpty():
                continue
            painter.drawPixmap(source_rect, item.pixmap, source_rect)
        return True

    @staticmethod
    def _draw_intersecting_tile_strips(
        painter: QPainter,
        item: RasterLayerRenderItem,
        repair_source_rect: QRectF,
    ) -> None:
        """Draw only tile payloads whose source rect intersects a repaired strip."""
        tile_size = item.tile_size
        if tile_size <= 0:
            return
        for tile_data in item.tiles_to_draw:
            tile_rect = QRectF(
                tile_data.draw_pos,
                QSizeF(tile_data.image.width(), tile_data.image.height()),
            )
            intersected = tile_rect.intersected(repair_source_rect)
            if intersected.isEmpty():
                continue
            source_offset = intersected.topLeft() - tile_data.draw_pos
            tile_source_rect = QRectF(source_offset, intersected.size())
            painter.drawImage(intersected, tile_data.image, tile_source_rect)

    def _allocate_dpi_buffer(self, physical_size: QSize, dpr: float) -> QImage:
        """Create an ARGB buffer tagged with the given DPR for the physical viewport size."""
        buffer = QImage(physical_size, QImage.Format_ARGB32_Premultiplied)
        buffer.setDevicePixelRatio(dpr)
        return buffer

    def _redraw_base_image_buffer(self, dirty_region: QRegion, plan: SceneRenderPlan):
        """Repaint the base buffer for the dirty region, clearing outside-image areas before drawing."""
        if self._base_image_buffer is None:
            return
        qpane_rect = plan.qpane_rect
        qpane_region = QRegion(qpane_rect)
        full_viewport_dirty = dirty_region.intersected(qpane_region) == qpane_region
        if full_viewport_dirty:
            dirty_region = dirty_region.united(self._widget_logical_buffer_region())
        if full_viewport_dirty:
            self._full_redraws += 1
        else:
            self._partial_redraws += 1
        self._current_render_plan = plan
        buffer_painter = QPainter(self._base_image_buffer)
        try:
            self._translate_to_widget_origin(
                buffer_painter,
                CoordinateContext(self.qpane),
            )
            base_only_item = self._base_only_raster_item(plan)
            if base_only_item is not None:
                self._redraw_base_only_dirty_region(
                    buffer_painter,
                    dirty_region,
                    plan,
                    base_only_item,
                )
                return
            base_item = plan.base_raster_item
            if base_item is None:
                buffer_painter.setClipRegion(dirty_region)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_Source)
                for rect in dirty_region:
                    buffer_painter.fillRect(rect, Qt.transparent)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                self._draw_visible_scene_items(buffer_painter, plan)
                return
            if plan.scene_bounds != base_item.placement:
                buffer_painter.setClipRegion(dirty_region)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_Source)
                for rect in dirty_region:
                    buffer_painter.fillRect(rect, Qt.transparent)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                self._draw_visible_scene_items(buffer_painter, plan)
                return
            # Image bounds in buffer coords (no double-transform).
            img_src = QRectF(
                0, 0, base_item.source_image.width(), base_item.source_image.height()
            )
            img_log = base_item.transform.mapRect(img_src)
            # Use aligned bounds with a small expansion to avoid rounding gaps.
            img_region = QRegion(img_log.toAlignedRect().adjusted(-1, -1, 1, 1))
            # Split the incoming dirty region into outside/inside parts.
            outside_region = dirty_region.subtracted(img_region)
            inside_region = dirty_region.intersected(img_region)
            # Phase A: clear outside-of-image dirty area (no drawing there).
            if not outside_region.isEmpty():
                buffer_painter.setClipRegion(outside_region)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_Source)
                for rect in outside_region:
                    buffer_painter.fillRect(rect, Qt.transparent)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # Phase B: clear inside-of-image dirty area, then draw.
            if not inside_region.isEmpty():
                buffer_painter.setClipRegion(inside_region)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_Source)
                for rect in inside_region:
                    buffer_painter.fillRect(rect, Qt.transparent)
                buffer_painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                self._draw_visible_scene_items(buffer_painter, plan)
        finally:
            buffer_painter.end()
            if full_viewport_dirty:
                self._buffer_pan = QPointF(plan.current_pan)
                self._subpixel_pan_offset = QPointF(0, 0)

    @staticmethod
    def _base_only_raster_item(plan: SceneRenderPlan) -> RasterLayerRenderItem | None:
        """Return the sole base raster item when a plan matches old-QPane shape."""
        if len(plan.render_items) != 1:
            return None
        item = plan.render_items[0]
        if not isinstance(item, RasterLayerRenderItem):
            return None
        if item is not plan.base_raster_item:
            return None
        if not item.descriptor.visible:
            return None
        if not isclose(item.descriptor.opacity, 1.0, rel_tol=0.0, abs_tol=1e-9):
            return None
        if item.clip is not None:
            return None
        if item.placement != plan.scene_bounds:
            return None
        if item.source_image.isNull():
            return None
        return item

    def _redraw_base_only_dirty_region(
        self,
        painter: QPainter,
        dirty_region: QRegion,
        plan: SceneRenderPlan,
        item: RasterLayerRenderItem,
    ) -> None:
        """Redraw a dirty region for a single full-scene base raster item."""
        image_rect = QRectF(0, 0, item.source_image.width(), item.source_image.height())
        image_region = QRegion(
            item.transform.mapRect(image_rect).toAlignedRect().adjusted(-1, -1, 1, 1)
        )
        outside_region = dirty_region.subtracted(image_region)
        inside_region = dirty_region.intersected(image_region)
        self._clear_dirty_region(painter, outside_region)
        if inside_region.isEmpty():
            return
        self._clear_dirty_region(painter, inside_region)
        painter.setClipRegion(inside_region)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.save()
        try:
            if item.render_hint_enabled:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setTransform(item.transform, True)
            if item.strategy == RenderStrategy.DIRECT:
                self._draw_direct_view(painter, item)
            elif item.strategy == RenderStrategy.TILE:
                self._draw_tiled_view(painter, plan, item)
        finally:
            painter.restore()

    @staticmethod
    def _clear_dirty_region(painter: QPainter, dirty_region: QRegion) -> None:
        """Clear a non-empty dirty region with source composition."""
        if dirty_region.isEmpty():
            return
        painter.setClipRegion(dirty_region)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        for rect in dirty_region:
            painter.fillRect(rect, Qt.transparent)

    def _draw_visible_scene_items(
        self, painter: QPainter, plan: SceneRenderPlan
    ) -> None:
        """Draw visible scene raster items in bottom-to-top order."""
        for item in plan.render_items:
            if not item.descriptor.visible:
                continue
            painter.save()
            try:
                if isinstance(item, RasterLayerRenderItem):
                    self._draw_raster_item(painter, plan, item)
                elif isinstance(item, MaskLayerRenderItem):
                    self._draw_mask_item(painter, plan, item)
            finally:
                painter.restore()

    def _draw_raster_item(
        self,
        painter: QPainter,
        plan: SceneRenderPlan,
        item: RasterLayerRenderItem,
    ) -> None:
        """Draw one raster image item."""
        if item.render_hint_enabled:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setTransform(item.transform, True)
        self._apply_layer_clip(painter, plan, item)
        if item.strategy == RenderStrategy.DIRECT:
            self._draw_direct_view(painter, item)
        elif item.strategy == RenderStrategy.TILE:
            self._draw_tiled_view(painter, plan, item)

    def _draw_mask_item(
        self,
        painter: QPainter,
        plan: SceneRenderPlan,
        item: MaskLayerRenderItem,
    ) -> None:
        """Draw one colorized mask item."""
        if item.render_hint_enabled:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setTransform(item.transform, True)
        self._apply_layer_clip(painter, plan, item)
        painter.setOpacity(item.descriptor.opacity)
        painter.drawPixmap(0, 0, item.pixmap)

    def _apply_layer_clip(
        self,
        painter: QPainter,
        plan: SceneRenderPlan,
        item: RasterLayerRenderItem | MaskLayerRenderItem,
    ) -> None:
        """Apply a layer clip in its declared coordinate space."""
        clip = item.clip
        if clip is None:
            return
        source_width, source_height = self._item_source_size(item)
        if clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_SCENE:
            scene_clip = QRectF(
                plan.scene_bounds.x + clip.x * plan.scene_bounds.width,
                plan.scene_bounds.y + clip.y * plan.scene_bounds.height,
                clip.width * plan.scene_bounds.width,
                clip.height * plan.scene_bounds.height,
            )
        elif clip.coordinate_space == ClipCoordinateSpace.SCENE:
            scene_clip = QRectF(clip.x, clip.y, clip.width, clip.height)
        elif clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_VIEWPORT:
            viewport_clip = QRectF(
                plan.qpane_rect.x() + clip.x * plan.qpane_rect.width(),
                plan.qpane_rect.y() + clip.y * plan.qpane_rect.height(),
                clip.width * plan.qpane_rect.width(),
                clip.height * plan.qpane_rect.height(),
            )
            rect = self._viewport_clip_to_source(item, viewport_clip)
            self._set_source_clip_rect(painter, rect)
            return
        elif clip.coordinate_space == ClipCoordinateSpace.VIEWPORT:
            rect = self._viewport_clip_to_source(
                item, QRectF(clip.x, clip.y, clip.width, clip.height)
            )
            self._set_source_clip_rect(painter, rect)
            return
        else:
            return
        rect = self._scene_clip_to_source(
            item,
            scene_clip,
            source_width=source_width,
            source_height=source_height,
        )
        self._set_source_clip_rect(painter, rect)

    @staticmethod
    def _item_source_size(
        item: RasterLayerRenderItem | MaskLayerRenderItem,
    ) -> tuple[int, int]:
        """Return source dimensions for the render item."""
        if isinstance(item, RasterLayerRenderItem):
            return item.source_image.width(), item.source_image.height()
        return item.pixmap.width(), item.pixmap.height()

    @staticmethod
    def _scene_clip_to_source(
        item: RasterLayerRenderItem | MaskLayerRenderItem,
        scene_clip: QRectF,
        *,
        source_width: int,
        source_height: int,
    ) -> QRectF:
        """Convert a scene-space clip into item source coordinates."""
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
    def _viewport_clip_to_source(
        item: RasterLayerRenderItem | MaskLayerRenderItem,
        viewport_clip: QRectF,
    ) -> QRectF:
        """Convert a viewport-space clip into item source coordinates."""
        inverse, invertible = item.transform.inverted()
        if not invertible:
            return QRectF()
        return inverse.mapRect(viewport_clip)

    @staticmethod
    def _set_source_clip_rect(painter: QPainter, rect: QRectF) -> None:
        """Set the painter clip in item source coordinates."""
        if rect.isEmpty():
            painter.setClipRect(QRectF())
            return
        painter.setClipRect(rect)

    def _draw_tile_debug_overlay(
        self,
        painter: QPainter,
        plan: SceneRenderPlan,
        item: RasterLayerRenderItem,
    ):
        """Draw a debug grid over the visible tiles using the current transform."""
        if not item.debug_draw_tile_grid:
            return
        max_cols, max_rows = item.max_tile_cols, item.max_tile_rows
        if max_cols <= 0 or max_rows <= 0:
            return
        tile_size = item.tile_size
        stride = max(1, tile_size - item.tile_overlap)
        visible_range = item.visible_tile_range
        if visible_range is None:
            return
        start_row, end_row, start_col, end_col = visible_range
        if start_row > end_row or start_col > end_col:
            return
        effective_zoom = plan.zoom / item.pyramid_scale
        pen = QPen(QColor(255, 0, 0, 100))
        pen.setWidthF(2.0 / effective_zoom)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for r in range(start_row, end_row + 1):
            for c in range(start_col, end_col + 1):
                draw_pos = QPointF(c * stride, r * stride)
                debug_rect = QRectF(draw_pos, QSizeF(tile_size, tile_size))
                painter.drawRect(debug_rect)

    def _draw_tiled_view(
        self,
        painter: QPainter,
        plan: SceneRenderPlan,
        item: RasterLayerRenderItem,
    ):
        """Draw the tiled view clipped to the image bounds using the item transform."""
        img_rect_src = QRectF(
            0, 0, item.source_image.width(), item.source_image.height()
        )
        painter.save()
        # Slight padding guards against subpixel rounding at the edges.
        painter.setClipRect(
            img_rect_src.adjusted(-0.5, -0.5, 0.5, 0.5),
            Qt.ClipOperation.IntersectClip,
        )
        painter.drawImage(0, 0, item.source_image)
        for tile_data in item.tiles_to_draw:
            painter.drawImage(tile_data.draw_pos, tile_data.image)
        if item.debug_draw_tile_grid:
            self._draw_tile_debug_overlay(painter, plan, item)
        painter.restore()

    def _draw_direct_view(self, painter: QPainter, item: RasterLayerRenderItem):
        """Draw the source image directly with no tiling helpers."""
        painter.drawImage(0, 0, item.source_image)

    @classmethod
    def _overscanned_buffer_size(cls, viewport_size: QSize) -> QSize:
        """Return the backing-buffer size including physical overscan."""
        margin = cls._BUFFER_OVERSCAN_PHYSICAL_PX * 2
        return QSize(
            max(0, viewport_size.width() + margin),
            max(0, viewport_size.height() + margin),
        )

    def _buffer_margin_logical(self, context: CoordinateContext) -> QPointF:
        """Return the overscan margin in widget logical units."""
        margin = float(self._BUFFER_OVERSCAN_PHYSICAL_PX)
        return context.physical_to_logical(QPointF(margin, margin))

    def _translate_to_widget_origin(
        self,
        painter: QPainter,
        context: CoordinateContext,
    ) -> None:
        """Move widget logical coordinates to their overscanned buffer origin."""
        painter.translate(self._buffer_margin_logical(context))

    def _buffer_physical_to_widget_logical(
        self,
        rect: QRectF,
        context: CoordinateContext,
    ) -> QRectF:
        """Convert a physical backing-buffer rect into widget logical coordinates."""
        margin = float(self._BUFFER_OVERSCAN_PHYSICAL_PX)
        viewport_rect = QRectF(
            rect.x() - margin,
            rect.y() - margin,
            rect.width(),
            rect.height(),
        )
        return context.physical_to_logical(viewport_rect)

    def _widget_logical_buffer_region(self) -> QRegion:
        """Return the full overscanned backing store in widget logical coordinates."""
        if self._base_image_buffer is None:
            return QRegion()
        context = CoordinateContext(self.qpane)
        rect = self._buffer_physical_to_widget_logical(
            QRectF(self._base_image_buffer.rect()),
            context,
        )
        return QRegion(rect.toAlignedRect())

    def _mark_diagnostics_dirty(self) -> None:
        """Mark render diagnostics dirty on the QPane if available."""
        diagnostics = getattr(self.qpane, "diagnostics", None)
        if not callable(diagnostics):
            return
        try:
            manager = diagnostics()
        except Exception:  # pragma: no cover - defensive guard
            return
        if manager is not None:
            manager.set_dirty("render")
