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
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, QSizeF
from PySide6.QtGui import QImage
from qpane.rendering.coordinates import (
    CoordinateContext,
    LogicalPoint,
    PhysicalPoint,
)
from qpane.scene.identity import default_catalog_asset_key, default_scene_id
from qpane.scene.model import LayerPlacement
from qpane.scene.render_plan import SceneContentSnapshot
import uuid


class _StubViewport:
    def __init__(self, *, zoom: float, pan: QPointF) -> None:
        self.zoom = float(zoom)
        self.pan = QPointF(pan)


class _StubStack:
    def __init__(self, viewport):
        self.viewport = viewport


class _StubQPane:
    def __init__(
        self,
        *,
        dpr: float,
        qpane_size: tuple[int, int],
        image_size: tuple[int, int],
        zoom: float,
        pan: tuple[float, float],
    ) -> None:
        self._dpr = float(dpr)
        self._size = QSize(*qpane_size)
        self.original_image = QImage(
            image_size[0], image_size[1], QImage.Format_ARGB32_Premultiplied
        )
        self.original_image.fill(0)
        self.viewport = _StubViewport(zoom=zoom, pan=QPointF(*pan))
        self._view = _StubStack(self.viewport)

    def devicePixelRatioF(self) -> float:
        return self._dpr

    def size(self) -> QSize:
        return QSize(self._size)

    def view(self):
        return self._view

    def content_snapshot(self) -> SceneContentSnapshot:
        """Return a content snapshot matching the stub image geometry."""
        image_id = uuid.uuid4()
        bounds = LayerPlacement(
            x=0.0,
            y=0.0,
            width=float(self.original_image.width()),
            height=float(self.original_image.height()),
        )
        return SceneContentSnapshot(
            scene_id=default_scene_id(image_id),
            base_asset_key=default_catalog_asset_key(
                image_id,
                revision=0,
                source_path=None,
            ),
            base_image_size=self.original_image.size(),
            scene_bounds=bounds,
            active_content_bounds=bounds,
            current_path=None,
        )


def _make_context(
    *,
    dpr: float = 2.0,
    qpane_size: tuple[int, int] = (400, 320),
    image_size: tuple[int, int] = (800, 600),
    zoom: float = 0.75,
    pan: tuple[float, float] = (12.0, -8.0),
) -> CoordinateContext:
    qpane = _StubQPane(
        dpr=dpr,
        qpane_size=qpane_size,
        image_size=image_size,
        zoom=zoom,
        pan=pan,
    )
    return CoordinateContext(qpane, content_snapshot=qpane.content_snapshot())


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _expected_image_point(context: CoordinateContext, panel_point: QPointF) -> QPoint:
    panel = LogicalPoint.from_qt(panel_point)
    pan_log = context._pan_logical()
    centered = panel.minus(context._qpane_center_logical)
    offset_log = centered.minus(pan_log)
    safe_zoom = context._safe_zoom()
    rel_image_log = offset_log.scaled(1.0 / safe_zoom)
    rel_image_phys = rel_image_log.to_physical(context.dpr)
    image_coords = rel_image_phys.plus(context._image_center)
    max_x = max(0, int(context.image_size.width) - 1)
    max_y = max(0, int(context.image_size.height) - 1)
    return QPoint(
        _clamp_int(round(image_coords.x), 0, max_x),
        _clamp_int(round(image_coords.y), 0, max_y),
    )


def _expected_panel_point(context: CoordinateContext, image_point: QPointF) -> QPointF:
    image_phys = PhysicalPoint.from_qt(image_point)
    offset_from_center = image_phys.minus(context._image_center)
    safe_zoom = context._safe_zoom()
    offset_panel_phys = offset_from_center.scaled(safe_zoom)
    offset_log = offset_panel_phys.to_logical(context.dpr)
    result_log = context._qpane_center_logical.plus(context._pan_logical()).plus(
        offset_log
    )
    return result_log.to_qt()


def _expected_scale(context: CoordinateContext, pyramid_scale: float) -> float:
    safe_zoom = context._safe_zoom()
    effective = safe_zoom / pyramid_scale if pyramid_scale != 0 else safe_zoom
    return effective / context.dpr if context.dpr > 0 else effective


def test_panel_to_image_respects_dpr():
    context = _make_context(dpr=2.4)
    panel_point = QPointF(200.5, 110.25)
    result = context.panel_to_image(panel_point)
    expected = _expected_image_point(context, panel_point)
    assert result == expected


def test_image_to_panel_respects_dpr():
    context = _make_context(dpr=1.75)
    image_point = QPointF(412.0, 278.0)
    result = context.image_to_panel(image_point)
    expected = _expected_panel_point(context, image_point)
    assert result.x() == pytest.approx(expected.x(), abs=1e-6)
    assert result.y() == pytest.approx(expected.y(), abs=1e-6)


def test_panel_image_round_trip_consistency():
    context = _make_context(dpr=2.0)
    panel_point = QPointF(180.5, 90.25)
    image_point = context.panel_to_image(panel_point)
    expected_image = _expected_image_point(context, panel_point)
    assert image_point == expected_image
    panel_round_trip = context.image_to_panel(QPointF(image_point))
    expected_panel = _expected_panel_point(context, QPointF(image_point))
    assert panel_round_trip.x() == pytest.approx(expected_panel.x(), abs=1e-6)
    assert panel_round_trip.y() == pytest.approx(expected_panel.y(), abs=1e-6)


def test_transform_scale_matches_expected():
    context = _make_context(dpr=2.3)
    transform = context.get_painter_transform(
        context.image_size.to_qt().toSize(), pyramid_scale=1.0
    )
    expected_scale = _expected_scale(context, pyramid_scale=1.0)
    assert transform.m11() == pytest.approx(expected_scale, abs=1e-9)
    assert transform.m22() == pytest.approx(expected_scale, abs=1e-9)
    source_point = QPointF(256.0, 192.0)
    mapped_panel = transform.map(source_point)
    expected_panel = _expected_panel_point(context, source_point)
    assert mapped_panel.x() == pytest.approx(expected_panel.x(), abs=1e-6)
    assert mapped_panel.y() == pytest.approx(expected_panel.y(), abs=1e-6)


def test_transform_handles_pyramid_scale():
    context = _make_context(dpr=1.6, image_size=(1024, 768))
    pyramid_scale = 0.5
    source_size = QSize(
        int(context.image_size.width * pyramid_scale),
        int(context.image_size.height * pyramid_scale),
    )
    transform = context.get_painter_transform(source_size, pyramid_scale=pyramid_scale)
    expected_scale = _expected_scale(context, pyramid_scale)
    assert transform.m11() == pytest.approx(expected_scale, abs=1e-9)
    assert transform.m22() == pytest.approx(expected_scale, abs=1e-9)
    source_point = QPointF(128.0, 96.0)
    original_point = QPointF(
        source_point.x() / pyramid_scale, source_point.y() / pyramid_scale
    )
    mapped_panel = transform.map(source_point)
    expected_panel = _expected_panel_point(context, original_point)
    assert mapped_panel.x() == pytest.approx(expected_panel.x(), abs=1e-6)
    assert mapped_panel.y() == pytest.approx(expected_panel.y(), abs=1e-6)


def test_logical_physical_helpers_handle_common_types():
    context = _make_context(dpr=1.5)
    scalar = 5.2
    scalar_phys = context.logical_to_physical(scalar)
    assert scalar_phys == pytest.approx(scalar * 1.5)
    assert context.physical_to_logical(scalar_phys) == pytest.approx(scalar)
    point = QPointF(3.0, -2.0)
    point_phys = context.logical_to_physical(point)
    assert point_phys.x() == pytest.approx(point.x() * 1.5)
    assert point_phys.y() == pytest.approx(point.y() * 1.5)
    point_back = context.physical_to_logical(point_phys)
    assert point_back.x() == pytest.approx(point.x())
    assert point_back.y() == pytest.approx(point.y())
    size = QSizeF(8.0, 5.5)
    size_phys = context.logical_to_physical(size)
    assert size_phys.width() == pytest.approx(size.width() * 1.5)
    assert size_phys.height() == pytest.approx(size.height() * 1.5)
    size_back = context.physical_to_logical(size_phys)
    assert size_back.width() == pytest.approx(size.width())
    assert size_back.height() == pytest.approx(size.height())
    rect = QRectF(QPointF(1.0, 2.0), QSizeF(6.0, 4.0))
    rect_phys = context.logical_to_physical(rect)
    assert rect_phys.topLeft().x() == pytest.approx(rect.topLeft().x() * 1.5)
    assert rect_phys.topLeft().y() == pytest.approx(rect.topLeft().y() * 1.5)
    assert rect_phys.size().width() == pytest.approx(rect.size().width() * 1.5)
    assert rect_phys.size().height() == pytest.approx(rect.size().height() * 1.5)
    rect_back = context.physical_to_logical(rect_phys)
    assert rect_back.topLeft().x() == pytest.approx(rect.topLeft().x())
    assert rect_back.topLeft().y() == pytest.approx(rect.topLeft().y())
    assert rect_back.size().width() == pytest.approx(rect.size().width())
    assert rect_back.size().height() == pytest.approx(rect.size().height())


def test_panel_to_image_clamps_to_image_bounds():
    context = _make_context()
    far_panel_point = QPointF(-500.0, 1000.0)
    image_point = context.panel_to_image(far_panel_point)
    assert image_point.x() == 0
    assert image_point.y() == context.image_size.height - 1


def test_panel_hit_test_reports_raw_coordinates():
    context = _make_context()
    panel_point = QPointF(-250.0, 75.0)
    hit = context.panel_to_image_hit(panel_point)
    assert hit.clamped_point.x() == 0
    assert hit.raw_point.x() < 0
    assert hit.inside_image is False
    assert context.panel_to_image(panel_point) == hit.clamped_point


def test_helpers_reject_unsupported_type():
    context = _make_context()
    with pytest.raises(TypeError):
        context.logical_to_physical(object())


def test_zero_zoom_falls_back_to_identity():
    context = _make_context(zoom=0.0, pan=(0.0, 0.0))
    qpane_center = QPointF(
        context.qpane_size_logical.width / 2.0,
        context.qpane_size_logical.height / 2.0,
    )
    image_point = context.panel_to_image(qpane_center)
    assert image_point.x() == pytest.approx(context.image_size.width / 2.0, abs=1)
    assert image_point.y() == pytest.approx(context.image_size.height / 2.0, abs=1)
    panel_point = context.image_to_panel(
        QPointF(context.image_size.width / 2.0, context.image_size.height / 2.0)
    )
    assert panel_point.x() == pytest.approx(qpane_center.x(), abs=1e-6)
    assert panel_point.y() == pytest.approx(qpane_center.y(), abs=1e-6)


def test_dpr_clamped_and_warns(caplog):
    caplog.set_level("WARNING", logger="qpane.rendering.coordinates")
    context = _make_context(dpr=0.0)
    assert context.dpr == pytest.approx(1.0)
    assert "falling back to 1.0" in caplog.text
