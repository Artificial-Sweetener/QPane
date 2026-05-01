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

"""Project clipped render-item boundaries into widget coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from PySide6.QtCore import QLineF, QPointF, QRectF

from ..scene.model import ClipCoordinateSpace, LayerClip, LayerPlacement
from ..scene.render_plan import RasterLayerRenderItem, SceneRenderPlan
from ..types import ComparisonOrientation


@dataclass(frozen=True, slots=True)
class ProjectedClipBoundary:
    """Projected comparison clip boundary owned by render geometry."""

    orientation: ComparisonOrientation
    item: RasterLayerRenderItem
    scene_bounds: LayerPlacement
    full_segment: QLineF
    visible_segment: QLineF | None
    scene_position: float
    hit_width: float

    def contains(self, point: QPointF) -> bool:
        """Return whether ``point`` is within hit tolerance of the visible segment."""
        segment = self.visible_segment
        if segment is None:
            return False
        return _distance_to_segment(point, segment) <= self.hit_width / 2.0

    def split_for_widget_point(self, point: QPointF) -> float | None:
        """Return the normalized split represented by a widget point."""
        inverse, invertible = self.item.transform.inverted()
        if not invertible:
            return None
        source_point = inverse.map(point)
        source_width = self.item.source_image.width()
        source_height = self.item.source_image.height()
        placement = self.item.placement
        if (
            source_width <= 0
            or source_height <= 0
            or placement.width <= 0.0
            or placement.height <= 0.0
        ):
            return None
        if self.orientation == ComparisonOrientation.HORIZONTAL:
            scene_value = (
                placement.y + source_point.y() * placement.height / source_height
            )
            denominator = self.scene_bounds.height
            origin = self.scene_bounds.y
        else:
            scene_value = (
                placement.x + source_point.x() * placement.width / source_width
            )
            denominator = self.scene_bounds.width
            origin = self.scene_bounds.x
        if denominator <= 0.0:
            return None
        return min(1.0, max(0.0, (scene_value - origin) / denominator))


def projected_comparison_boundary(
    plan: SceneRenderPlan,
    *,
    orientation: ComparisonOrientation,
    hit_width: float,
) -> ProjectedClipBoundary | None:
    """Return the projected boundary for the active comparison render item."""
    item = _comparison_item(plan)
    if item is None or item.clip is None:
        return None
    scene_clip = _clip_to_scene_rect(plan, item.clip)
    if scene_clip is None:
        return None
    source_line = _scene_boundary_to_source_line(
        item,
        scene_clip,
        orientation=orientation,
    )
    if source_line is None:
        return None
    full_segment = QLineF(
        item.transform.map(source_line.p1()),
        item.transform.map(source_line.p2()),
    )
    visible_segment = _clip_segment_to_rect(full_segment, QRectF(plan.qpane_rect))
    scene_position = (
        scene_clip.top()
        if orientation == ComparisonOrientation.HORIZONTAL
        else scene_clip.left()
    )
    return ProjectedClipBoundary(
        orientation=orientation,
        item=item,
        scene_bounds=plan.scene_bounds,
        full_segment=full_segment,
        visible_segment=visible_segment,
        scene_position=scene_position,
        hit_width=hit_width,
    )


def _comparison_item(plan: SceneRenderPlan) -> RasterLayerRenderItem | None:
    """Return the active comparison raster item from ``plan``."""
    for item in plan.render_items:
        if not isinstance(item, RasterLayerRenderItem):
            continue
        if item.clip is None or not item.descriptor.visible:
            continue
        if item.descriptor.hit_test.role == "comparison-image":
            return item
    return None


def _clip_to_scene_rect(plan: SceneRenderPlan, clip: LayerClip) -> QRectF | None:
    """Convert a supported layer clip to scene coordinates."""
    if clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_SCENE:
        return QRectF(
            plan.scene_bounds.x + clip.x * plan.scene_bounds.width,
            plan.scene_bounds.y + clip.y * plan.scene_bounds.height,
            clip.width * plan.scene_bounds.width,
            clip.height * plan.scene_bounds.height,
        )
    if clip.coordinate_space == ClipCoordinateSpace.SCENE:
        return QRectF(clip.x, clip.y, clip.width, clip.height)
    return None


def _scene_boundary_to_source_line(
    item: RasterLayerRenderItem,
    scene_clip: QRectF,
    *,
    orientation: ComparisonOrientation,
) -> QLineF | None:
    """Convert the comparison clip boundary from scene to source coordinates."""
    placement = item.placement
    source_width = item.source_image.width()
    source_height = item.source_image.height()
    if (
        source_width <= 0
        or source_height <= 0
        or placement.width <= 0.0
        or placement.height <= 0.0
    ):
        return None
    left = _scene_x_to_source_x(scene_clip.left(), placement, source_width)
    right = _scene_x_to_source_x(scene_clip.right(), placement, source_width)
    top = _scene_y_to_source_y(scene_clip.top(), placement, source_height)
    bottom = _scene_y_to_source_y(scene_clip.bottom(), placement, source_height)
    if orientation == ComparisonOrientation.HORIZONTAL:
        return QLineF(QPointF(left, top), QPointF(right, top))
    return QLineF(QPointF(left, top), QPointF(left, bottom))


def _scene_x_to_source_x(
    scene_x: float, placement: LayerPlacement, source_width: int
) -> float:
    """Map a scene x coordinate into item source x coordinates."""
    return (scene_x - placement.x) * source_width / placement.width


def _scene_y_to_source_y(
    scene_y: float, placement: LayerPlacement, source_height: int
) -> float:
    """Map a scene y coordinate into item source y coordinates."""
    return (scene_y - placement.y) * source_height / placement.height


def _clip_segment_to_rect(line: QLineF, rect: QRectF) -> QLineF | None:
    """Clip ``line`` to ``rect`` using Liang-Barsky clipping."""
    if rect.isEmpty():
        return None
    x0 = line.p1().x()
    y0 = line.p1().y()
    x1 = line.p2().x()
    y1 = line.p2().y()
    dx = x1 - x0
    dy = y1 - y0
    p_values = (-dx, dx, -dy, dy)
    q_values = (
        x0 - rect.left(),
        rect.right() - x0,
        y0 - rect.top(),
        rect.bottom() - y0,
    )
    start = 0.0
    end = 1.0
    for p_value, q_value in zip(p_values, q_values):
        if p_value == 0.0:
            if q_value < 0.0:
                return None
            continue
        ratio = q_value / p_value
        if p_value < 0.0:
            start = max(start, ratio)
        else:
            end = min(end, ratio)
        if start > end:
            return None
    return QLineF(
        QPointF(x0 + start * dx, y0 + start * dy),
        QPointF(x0 + end * dx, y0 + end * dy),
    )


def _distance_to_segment(point: QPointF, segment: QLineF) -> float:
    """Return the shortest distance from ``point`` to ``segment``."""
    x0 = point.x()
    y0 = point.y()
    x1 = segment.p1().x()
    y1 = segment.p1().y()
    x2 = segment.p2().x()
    y2 = segment.p2().y()
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return hypot(x0 - x1, y0 - y1)
    ratio = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / length_sq))
    projection_x = x1 + ratio * dx
    projection_y = y1 + ratio * dy
    return hypot(x0 - projection_x, y0 - projection_y)
