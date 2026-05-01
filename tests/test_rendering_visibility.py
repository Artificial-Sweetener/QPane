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

"""Tests for render-planning visibility geometry."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QTransform

from qpane.rendering.visibility import visible_source_rect_for_layer
from qpane.scene.model import ClipCoordinateSpace, LayerClip, LayerPlacement


def _placement(
    x: float = 0.0,
    y: float = 0.0,
    width: float = 100.0,
    height: float = 100.0,
) -> LayerPlacement:
    """Return a layer placement for visibility tests."""
    return LayerPlacement(x=x, y=y, width=width, height=height)


def _rect_tuple(rect: QRectF) -> tuple[float, float, float, float]:
    """Return rounded rectangle components for stable assertions."""
    return (
        round(rect.x(), 4),
        round(rect.y(), 4),
        round(rect.width(), 4),
        round(rect.height(), 4),
    )


def test_visible_source_rect_intersects_viewport_and_layer() -> None:
    """Viewport-scene overlap should project into source coordinates."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(width=200.0, height=100.0),
        layer_placement=_placement(x=50.0, y=0.0, width=100.0, height=100.0),
        source_size=QSize(200, 100),
        visible_scene_rect=QRectF(0.0, 25.0, 100.0, 50.0),
        clip=None,
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
    )

    assert result is not None
    assert _rect_tuple(result.scene_rect) == (50.0, 25.0, 50.0, 50.0)
    assert _rect_tuple(result.source_rect) == (0.0, 25.0, 100.0, 50.0)


def test_visible_source_rect_returns_none_for_empty_layer_intersection() -> None:
    """Layers outside the visible scene rect should have no visible source area."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(),
        layer_placement=_placement(x=150.0, y=0.0),
        source_size=QSize(100, 100),
        visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        clip=None,
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
    )

    assert result is None


def test_normalized_scene_clip_limits_source_rect() -> None:
    """Normalized scene clips should limit source visibility."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(width=200.0, height=100.0),
        layer_placement=_placement(width=200.0, height=100.0),
        source_size=QSize(400, 200),
        visible_scene_rect=QRectF(0.0, 0.0, 200.0, 100.0),
        clip=LayerClip(
            coordinate_space=ClipCoordinateSpace.NORMALIZED_SCENE,
            x=0.25,
            y=0.0,
            width=0.5,
            height=1.0,
        ),
        viewport_rect=QRectF(0.0, 0.0, 200.0, 100.0),
    )

    assert result is not None
    assert _rect_tuple(result.source_rect) == (100.0, 0.0, 200.0, 200.0)


def test_scene_clip_limits_source_rect() -> None:
    """Scene-space clips should limit source visibility."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(width=200.0, height=100.0),
        layer_placement=_placement(width=200.0, height=100.0),
        source_size=QSize(400, 200),
        visible_scene_rect=QRectF(0.0, 0.0, 200.0, 100.0),
        clip=LayerClip(
            coordinate_space=ClipCoordinateSpace.SCENE,
            x=50.0,
            y=25.0,
            width=100.0,
            height=50.0,
        ),
        viewport_rect=QRectF(0.0, 0.0, 200.0, 100.0),
    )

    assert result is not None
    assert _rect_tuple(result.source_rect) == (100.0, 50.0, 200.0, 100.0)


def test_normalized_viewport_clip_limits_source_rect() -> None:
    """Normalized viewport clips should limit source visibility through transform."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(),
        layer_placement=_placement(),
        source_size=QSize(100, 100),
        visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        clip=LayerClip(
            coordinate_space=ClipCoordinateSpace.NORMALIZED_VIEWPORT,
            x=0.5,
            y=0.0,
            width=0.5,
            height=1.0,
        ),
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        item_transform=QTransform(),
    )

    assert result is not None
    assert _rect_tuple(result.source_rect) == (50.0, 0.0, 50.0, 100.0)


def test_viewport_clip_limits_source_rect() -> None:
    """Viewport clips should limit source visibility through transform."""
    transform = QTransform()
    transform.scale(2.0, 2.0)

    result = visible_source_rect_for_layer(
        scene_bounds=_placement(),
        layer_placement=_placement(),
        source_size=QSize(100, 100),
        visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        clip=LayerClip(
            coordinate_space=ClipCoordinateSpace.VIEWPORT,
            x=50.0,
            y=20.0,
            width=50.0,
            height=40.0,
        ),
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        item_transform=transform,
    )

    assert result is not None
    assert _rect_tuple(result.source_rect) == (25.0, 10.0, 25.0, 20.0)


def test_clip_outside_layer_returns_none() -> None:
    """Clip regions outside the layer should remove layer visibility."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(),
        layer_placement=_placement(),
        source_size=QSize(100, 100),
        visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        clip=LayerClip(
            coordinate_space=ClipCoordinateSpace.SCENE,
            x=200.0,
            y=0.0,
            width=50.0,
            height=50.0,
        ),
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
    )

    assert result is None


def test_invalid_layer_geometry_returns_none() -> None:
    """Invalid source or placement dimensions should produce no visibility."""
    assert (
        visible_source_rect_for_layer(
            scene_bounds=_placement(),
            layer_placement=_placement(width=0.0),
            source_size=QSize(100, 100),
            visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
            clip=None,
            viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        )
        is None
    )
    assert (
        visible_source_rect_for_layer(
            scene_bounds=_placement(),
            layer_placement=_placement(),
            source_size=QSize(),
            visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
            clip=None,
            viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        )
        is None
    )


def test_source_rect_is_clamped_to_source_bounds() -> None:
    """Visibility should never exceed source image bounds."""
    result = visible_source_rect_for_layer(
        scene_bounds=_placement(),
        layer_placement=_placement(x=-50.0, y=-50.0, width=200.0, height=200.0),
        source_size=QSize(100, 100),
        visible_scene_rect=QRectF(0.0, 0.0, 100.0, 100.0),
        clip=None,
        viewport_rect=QRectF(0.0, 0.0, 100.0, 100.0),
    )

    assert result is not None
    assert _rect_tuple(result.source_rect) == (25.0, 25.0, 50.0, 50.0)
