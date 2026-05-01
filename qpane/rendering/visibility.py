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

"""Layer visibility geometry used by render planning."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QTransform

from ..scene.model import ClipCoordinateSpace, LayerClip, LayerPlacement


@dataclass(frozen=True, slots=True)
class LayerVisibility:
    """Source and scene visibility for one render-planned layer."""

    scene_rect: QRectF
    source_rect: QRectF

    def __post_init__(self) -> None:
        """Detach mutable Qt rectangles from caller-owned geometry."""
        object.__setattr__(self, "scene_rect", QRectF(self.scene_rect))
        object.__setattr__(self, "source_rect", QRectF(self.source_rect))

    @property
    def is_empty(self) -> bool:
        """Return True when the layer contributes no visible source pixels."""
        return self.scene_rect.isEmpty() or self.source_rect.isEmpty()


def visible_source_rect_for_layer(
    *,
    scene_bounds: LayerPlacement,
    layer_placement: LayerPlacement,
    source_size: QSize,
    visible_scene_rect: QRectF,
    clip: LayerClip | None,
    viewport_rect: QRectF,
    item_transform: QTransform | None = None,
) -> LayerVisibility | None:
    """Return the source-space rectangle that can contribute pixels."""
    if source_size.width() <= 0 or source_size.height() <= 0:
        return None
    if layer_placement.width <= 0.0 or layer_placement.height <= 0.0:
        return None

    layer_scene_rect = _placement_rect(layer_placement)
    scene_visible = QRectF(visible_scene_rect).intersected(layer_scene_rect)
    if scene_visible.isEmpty():
        return None

    if clip is None:
        return _visibility_from_scene_rect(
            scene_visible,
            layer_placement=layer_placement,
            source_size=source_size,
        )

    if clip.coordinate_space in {
        ClipCoordinateSpace.NORMALIZED_SCENE,
        ClipCoordinateSpace.SCENE,
    }:
        clip_scene = _clip_to_scene_rect(clip, scene_bounds)
        clipped_scene = scene_visible.intersected(clip_scene)
        if clipped_scene.isEmpty():
            return None
        return _visibility_from_scene_rect(
            clipped_scene,
            layer_placement=layer_placement,
            source_size=source_size,
        )

    if clip.coordinate_space in {
        ClipCoordinateSpace.NORMALIZED_VIEWPORT,
        ClipCoordinateSpace.VIEWPORT,
    }:
        source_visible = _scene_rect_to_source_rect(
            scene_visible,
            layer_placement=layer_placement,
            source_size=source_size,
        )
        clip_source = _viewport_clip_to_source_rect(
            clip,
            viewport_rect=viewport_rect,
            item_transform=item_transform,
        )
        if clip_source is None:
            return None
        clipped_source = source_visible.intersected(clip_source)
        return _visibility_from_source_rect(
            clipped_source,
            layer_placement=layer_placement,
            source_size=source_size,
        )

    return None


def _visibility_from_scene_rect(
    scene_rect: QRectF,
    *,
    layer_placement: LayerPlacement,
    source_size: QSize,
) -> LayerVisibility | None:
    """Build visibility by converting a scene-space rectangle to source space."""
    source_rect = _scene_rect_to_source_rect(
        scene_rect,
        layer_placement=layer_placement,
        source_size=source_size,
    )
    return _visibility_from_source_rect(
        source_rect,
        layer_placement=layer_placement,
        source_size=source_size,
    )


def _visibility_from_source_rect(
    source_rect: QRectF,
    *,
    layer_placement: LayerPlacement,
    source_size: QSize,
) -> LayerVisibility | None:
    """Build visibility by clamping a source-space rectangle to source bounds."""
    clamped_source = _clamp_to_source_bounds(source_rect, source_size)
    if clamped_source.isEmpty():
        return None
    scene_rect = _source_rect_to_scene_rect(
        clamped_source,
        layer_placement=layer_placement,
        source_size=source_size,
    )
    if scene_rect.isEmpty():
        return None
    return LayerVisibility(scene_rect=scene_rect, source_rect=clamped_source)


def _placement_rect(placement: LayerPlacement) -> QRectF:
    """Return a QRectF for a scene layer placement."""
    return QRectF(placement.x, placement.y, placement.width, placement.height)


def _clip_to_scene_rect(clip: LayerClip, scene_bounds: LayerPlacement) -> QRectF:
    """Convert a scene-space clip variant into scene coordinates."""
    if clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_SCENE:
        return QRectF(
            scene_bounds.x + clip.x * scene_bounds.width,
            scene_bounds.y + clip.y * scene_bounds.height,
            clip.width * scene_bounds.width,
            clip.height * scene_bounds.height,
        )
    return QRectF(clip.x, clip.y, clip.width, clip.height)


def _viewport_clip_to_source_rect(
    clip: LayerClip,
    *,
    viewport_rect: QRectF,
    item_transform: QTransform | None,
) -> QRectF | None:
    """Convert a viewport-space clip variant into source coordinates."""
    if item_transform is None:
        return None
    inverse, invertible = item_transform.inverted()
    if not invertible:
        return None
    if clip.coordinate_space == ClipCoordinateSpace.NORMALIZED_VIEWPORT:
        clip_rect = QRectF(
            viewport_rect.x() + clip.x * viewport_rect.width(),
            viewport_rect.y() + clip.y * viewport_rect.height(),
            clip.width * viewport_rect.width(),
            clip.height * viewport_rect.height(),
        )
    else:
        clip_rect = QRectF(clip.x, clip.y, clip.width, clip.height)
    if clip_rect.isEmpty():
        return QRectF()
    return inverse.mapRect(clip_rect)


def _scene_rect_to_source_rect(
    scene_rect: QRectF,
    *,
    layer_placement: LayerPlacement,
    source_size: QSize,
) -> QRectF:
    """Convert a scene-space rectangle into layer source coordinates."""
    return QRectF(
        (scene_rect.x() - layer_placement.x)
        * source_size.width()
        / layer_placement.width,
        (scene_rect.y() - layer_placement.y)
        * source_size.height()
        / layer_placement.height,
        scene_rect.width() * source_size.width() / layer_placement.width,
        scene_rect.height() * source_size.height() / layer_placement.height,
    )


def _source_rect_to_scene_rect(
    source_rect: QRectF,
    *,
    layer_placement: LayerPlacement,
    source_size: QSize,
) -> QRectF:
    """Convert a layer source-space rectangle into scene coordinates."""
    return QRectF(
        layer_placement.x
        + source_rect.x() * layer_placement.width / source_size.width(),
        layer_placement.y
        + source_rect.y() * layer_placement.height / source_size.height(),
        source_rect.width() * layer_placement.width / source_size.width(),
        source_rect.height() * layer_placement.height / source_size.height(),
    )


def _clamp_to_source_bounds(source_rect: QRectF, source_size: QSize) -> QRectF:
    """Clamp a source-space rectangle to image bounds."""
    bounds = QRectF(0.0, 0.0, float(source_size.width()), float(source_size.height()))
    if source_rect.isEmpty() or bounds.isEmpty():
        return QRectF()
    return QRectF(source_rect).intersected(bounds)
