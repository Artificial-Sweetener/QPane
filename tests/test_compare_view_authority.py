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

"""Tests for comparison image authority over fit and native zoom geometry."""

from __future__ import annotations

import uuid

import pytest
from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QImage, Qt

from qpane import ComparisonOrientation, QPane
from qpane.scene.render_plan import RasterLayerRenderItem


def _solid_image(
    width: int,
    height: int,
    color: Qt.GlobalColor = Qt.white,
) -> QImage:
    """Return a solid premultiplied image for authority tests."""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def _viewer_with_comparison(
    qapp,
    *,
    base_size: tuple[int, int],
    compare_size: tuple[int, int],
) -> tuple[QPane, uuid.UUID, uuid.UUID]:
    """Return a viewer with active vertical comparison."""
    viewer = QPane(features=())
    viewer.resize(400, 200)
    base_id = uuid.uuid4()
    compare_id = uuid.uuid4()
    viewer.setImagesByID(
        QPane.imageMapFromLists(
            [
                _solid_image(*base_size, color=Qt.red),
                _solid_image(*compare_size, color=Qt.blue),
            ],
            [None, None],
            [base_id, compare_id],
        ),
        base_id,
    )
    viewer.setComparisonImageID(compare_id)
    viewer.setComparisonSplit(0.5, ComparisonOrientation.VERTICAL)
    qapp.processEvents()
    return viewer, base_id, compare_id


def _cleanup_qpane(viewer: QPane, qapp) -> None:
    """Release a test widget through Qt's event loop."""
    viewer.deleteLater()
    qapp.processEvents()


def _raster_items(viewer: QPane) -> tuple[RasterLayerRenderItem, ...]:
    """Return raster items from the current render plan."""
    plan = viewer.view().calculateRenderPlan(is_blank=False)
    assert plan is not None
    return tuple(
        item for item in plan.render_items if isinstance(item, RasterLayerRenderItem)
    )


def test_compare_base_larger_keeps_base_authority(qapp) -> None:
    """Base-larger comparisons should keep base image geometry authoritative."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        base_size=(200, 100),
        compare_size=(100, 50),
    )
    try:
        plan = viewer.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert plan.scene_bounds.width == pytest.approx(200.0)
        assert plan.scene_bounds.height == pytest.approx(100.0)
        assert viewer.view().viewport.content_size == QSize(200, 100)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_compare_source_larger_sets_scene_authority(qapp) -> None:
    """Comparison-larger views should size the scene from the comparison image."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        base_size=(100, 50),
        compare_size=(200, 100),
    )
    try:
        plan = viewer.view().calculateRenderPlan(is_blank=False)
        base_item, compare_item = _raster_items(viewer)

        assert plan is not None
        assert plan.scene_bounds.width == pytest.approx(200.0)
        assert plan.scene_bounds.height == pytest.approx(100.0)
        assert base_item.placement.width == pytest.approx(200.0)
        assert base_item.placement.height == pytest.approx(100.0)
        assert compare_item.placement.width == pytest.approx(200.0)
        assert compare_item.placement.height == pytest.approx(100.0)
        assert viewer.view().viewport.content_size == QSize(200, 100)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_compare_fit_uses_larger_authority(qapp) -> None:
    """Fit zoom should use the larger compared image as content size."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        base_size=(100, 50),
        compare_size=(200, 100),
    )
    try:
        viewer.setZoomFit()

        viewport_size = viewer.physicalViewportRect().size()
        expected_zoom = min(
            viewport_size.width() / 200.0, viewport_size.height() / 100.0
        )
        assert viewer.view().viewport.zoom == pytest.approx(expected_zoom)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_compare_one_to_one_uses_larger_authority_geometry(qapp) -> None:
    """1:1 zoom should make the larger comparison source native-sized."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        base_size=(100, 50),
        compare_size=(200, 100),
    )
    try:
        viewer.setZoom1To1()
        _base_item, compare_item = _raster_items(viewer)

        source_rect = QRectF(0.0, 0.0, 200.0, 100.0)
        panel_rect = compare_item.transform.mapRect(source_rect)
        assert panel_rect.width() == pytest.approx(200.0)
        assert panel_rect.height() == pytest.approx(100.0)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_compare_clearing_restores_base_authority(qapp) -> None:
    """Clearing comparison should return content geometry to the base image."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        base_size=(100, 50),
        compare_size=(200, 100),
    )
    try:
        assert viewer.view().viewport.content_size == QSize(200, 100)

        viewer.clearComparisonImage()
        plan = viewer.view().calculateRenderPlan(is_blank=False)

        assert plan is not None
        assert plan.scene_bounds.width == pytest.approx(100.0)
        assert plan.scene_bounds.height == pytest.approx(50.0)
        assert viewer.view().viewport.content_size == QSize(100, 50)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_compare_replacing_source_updates_authority(qapp) -> None:
    """Replacing the comparison image should recalculate authority size."""
    viewer, _base_id, compare_id = _viewer_with_comparison(
        qapp,
        base_size=(100, 50),
        compare_size=(200, 100),
    )
    try:
        assert viewer.view().viewport.content_size == QSize(200, 100)

        viewer.addImage(compare_id, _solid_image(50, 25, Qt.green), None)
        plan = viewer.view().calculateRenderPlan(is_blank=False)

        assert viewer.comparisonState().enabled is True
        assert plan is not None
        assert plan.scene_bounds.width == pytest.approx(100.0)
        assert plan.scene_bounds.height == pytest.approx(50.0)
        assert viewer.view().viewport.content_size == QSize(100, 50)
    finally:
        _cleanup_qpane(viewer, qapp)
