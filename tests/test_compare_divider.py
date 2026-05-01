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

"""Tests for comparison divider interaction and host-owned drawing state."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from PySide6.QtCore import QEvent, QLineF, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent

import qpane
from qpane import ComparisonOrientation, ExtensionTool, QPane
from qpane.rendering.clip_geometry import projected_comparison_boundary
from qpane.scene.render_plan import SceneRenderPlan


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _solid_image(
    width: int = 100,
    height: int = 100,
    color: Qt.GlobalColor = Qt.white,
) -> QImage:
    """Return a solid image for comparison tests."""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def _make_mouse_event(
    event_type: QEvent.Type,
    point: QPointF,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton | None = None,
) -> QMouseEvent:
    """Return a mouse event positioned in logical widget coordinates."""
    active_buttons = buttons if buttons is not None else button
    return QMouseEvent(
        event_type,
        point,
        point,
        point,
        button,
        active_buttons,
        Qt.KeyboardModifier.NoModifier,
        Qt.MouseEventSource.MouseEventNotSynthesized,
    )


def _viewer_with_comparison(
    qapp,
    *,
    base_size: tuple[int, int] = (100, 100),
    compare_size: tuple[int, int] = (100, 100),
    orientation: ComparisonOrientation = ComparisonOrientation.VERTICAL,
    split: float = 0.5,
) -> tuple[QPane, uuid.UUID, uuid.UUID]:
    """Return a QPane with two catalog images and active comparison."""
    viewer = QPane(features=())
    viewer.resize(160, 160)
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
    viewer.setComparisonSplit(split, orientation)
    viewer.setZoom1To1()
    qapp.processEvents()
    return viewer, base_id, compare_id


def _cleanup_qpane(viewer: QPane, qapp) -> None:
    """Release a test widget through Qt's event loop."""
    viewer.deleteLater()
    qapp.processEvents()


def _render_buffer(viewer: QPane) -> tuple[SceneRenderPlan, QImage]:
    """Render the current plan and return a detached renderer buffer."""
    viewer.view().allocate_buffers()
    viewer.view().mark_dirty()
    plan = viewer.view().calculateRenderPlan(is_blank=False)
    assert plan is not None
    viewer.view().renderer.paint(plan)
    buffer = viewer.view().renderer.get_base_buffer()
    assert buffer is not None
    return plan, buffer.copy()


def _first_blue_x(buffer: QImage, y: int) -> int | None:
    """Return the first blue pixel in row ``y`` after at least one red pixel."""
    saw_red = False
    for x in range(buffer.width()):
        color = buffer.pixelColor(x, y)
        if color == QColor(Qt.red):
            saw_red = True
        elif saw_red and color == QColor(Qt.blue):
            return x
    return None


def _first_blue_y(buffer: QImage, x: int) -> int | None:
    """Return the first blue pixel in column ``x`` after at least one red pixel."""
    saw_red = False
    for y in range(buffer.height()):
        color = buffer.pixelColor(x, y)
        if color == QColor(Qt.red):
            saw_red = True
        elif saw_red and color == QColor(Qt.blue):
            return y
    return None


def _segment_midpoint(segment: QLineF) -> QPointF:
    """Return the midpoint of a projected segment."""
    return segment.pointAt(0.5)


def _segment_x(segment: QLineF) -> float:
    """Return the x coordinate for a vertical segment."""
    assert segment.p1().x() == pytest.approx(segment.p2().x())
    return segment.p1().x()


def _segment_y(segment: QLineF) -> float:
    """Return the y coordinate for a horizontal segment."""
    assert segment.p1().y() == pytest.approx(segment.p2().y())
    return segment.p1().y()


def _assert_vertical_segment_matches_rendered_transition(viewer: QPane) -> None:
    """Assert vertical divider state matches the rendered pixel transition."""
    plan, buffer = _render_buffer(viewer)
    divider = viewer.comparisonDividerState()
    helper_geometry = projected_comparison_boundary(
        plan,
        orientation=ComparisonOrientation.VERTICAL,
        hit_width=divider.hit_width,
    )
    assert divider.visible_segment is not None
    assert helper_geometry is not None
    assert helper_geometry.visible_segment is not None
    midpoint = _segment_midpoint(divider.visible_segment)
    transition_x = _first_blue_x(buffer, round(midpoint.y()))
    assert transition_x is not None
    assert _segment_x(divider.visible_segment) == pytest.approx(
        transition_x,
        abs=1.0,
    )
    assert _segment_x(helper_geometry.visible_segment) == pytest.approx(
        transition_x,
        abs=1.0,
    )


def _assert_horizontal_segment_matches_rendered_transition(viewer: QPane) -> None:
    """Assert horizontal divider state matches the rendered pixel transition."""
    plan, buffer = _render_buffer(viewer)
    divider = viewer.comparisonDividerState()
    helper_geometry = projected_comparison_boundary(
        plan,
        orientation=ComparisonOrientation.HORIZONTAL,
        hit_width=divider.hit_width,
    )
    assert divider.visible_segment is not None
    assert helper_geometry is not None
    assert helper_geometry.visible_segment is not None
    midpoint = _segment_midpoint(divider.visible_segment)
    transition_y = _first_blue_y(buffer, round(midpoint.x()))
    assert transition_y is not None
    assert _segment_y(divider.visible_segment) == pytest.approx(
        transition_y,
        abs=1.0,
    )
    assert _segment_y(helper_geometry.visible_segment) == pytest.approx(
        transition_y,
        abs=1.0,
    )


def test_public_api_exposes_divider_state_not_style() -> None:
    """QPane should expose host-drawing state, not a painted-divider style type."""
    assert "ComparisonDividerState" in qpane.__all__
    assert hasattr(qpane, "ComparisonDividerState")
    assert "ComparisonDividerStyle" not in qpane.__all__
    assert not hasattr(qpane, "ComparisonDividerStyle")


def test_default_comparison_has_no_qpane_painted_divider_path(qapp) -> None:
    """The divider should be interactive without a QPane-owned paint hook."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        divider = viewer.comparisonDividerState()

        assert divider.enabled is True
        assert divider.interactive is True
        assert divider.hit_width == 12.0
        assert divider.visible_segment is not None
        assert viewer.comparisonDividerInteraction().hit_test(
            _segment_midpoint(divider.visible_segment)
        )
        assert not hasattr(viewer.comparisonDividerInteraction(), "draw_overlay")
    finally:
        _cleanup_qpane(viewer, qapp)


def test_comparison_divider_state_returns_copied_line_values(qapp) -> None:
    """Public divider lines should not share mutable internal snapshot objects."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        first = viewer.comparisonDividerState()
        assert first.full_segment is not None
        assert first.visible_segment is not None
        first.full_segment.setP1(QPointF(-999.0, -999.0))
        first.visible_segment.setP1(QPointF(-999.0, -999.0))

        second = viewer.comparisonDividerState()
        assert second.full_segment is not None
        assert second.visible_segment is not None
        assert second.full_segment.p1() != QPointF(-999.0, -999.0)
        assert second.visible_segment.p1() != QPointF(-999.0, -999.0)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_vertical_divider_state_matches_rendered_transition(qapp) -> None:
    """Vertical divider state should match the red/blue pixel boundary."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        _assert_vertical_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_vertical_divider_state_moves_with_pan(qapp) -> None:
    """Vertical divider state should pan with the rendered comparison boundary."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        viewer.setPan(QPointF(24.0, 0.0))

        _assert_vertical_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_vertical_divider_state_moves_with_zoom(qapp) -> None:
    """Vertical divider state should scale with the rendered comparison boundary."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        viewer.applyZoom(1.4, anchor=QPointF(80.0, 80.0))

        _assert_vertical_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_vertical_divider_has_no_viewport_hit_when_boundary_is_offscreen(qapp) -> None:
    """An offscreen comparison boundary should not leave a viewport-fixed hit line."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        viewer.applyZoom(4.0, anchor=QPointF(80.0, 80.0))
        viewer.setPan(QPointF(100.0, 0.0))
        divider = viewer.comparisonDividerState()

        assert divider.enabled is True
        assert divider.full_segment is not None
        assert divider.visible_segment is None
        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                QPointF(80.0, 80.0),
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )
        assert viewer.cursor().shape() != Qt.CursorShape.SizeHorCursor
    finally:
        _cleanup_qpane(viewer, qapp)


def test_horizontal_divider_state_matches_rendered_transition(qapp) -> None:
    """Horizontal divider state should match the red/blue pixel boundary."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        orientation=ComparisonOrientation.HORIZONTAL,
    )
    try:
        _assert_horizontal_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_horizontal_divider_state_moves_with_pan(qapp) -> None:
    """Horizontal divider state should pan with the rendered comparison boundary."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        orientation=ComparisonOrientation.HORIZONTAL,
    )
    try:
        viewer.setPan(QPointF(0.0, 24.0))

        _assert_horizontal_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_mismatched_size_comparison_divider_matches_rendered_transition(qapp) -> None:
    """Divider state should use placement, not comparison source dimensions."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        compare_size=(50, 100),
    )
    try:
        _assert_vertical_segment_matches_rendered_transition(viewer)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_divider_hit_target_tracks_real_boundary(qapp) -> None:
    """Hit testing should use distance to the rendered boundary segment."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        divider = viewer.comparisonDividerState()
        assert divider.visible_segment is not None
        midpoint = _segment_midpoint(divider.visible_segment)

        assert viewer.comparisonDividerInteraction().hit_test(
            QPointF(midpoint.x() + 5.9, midpoint.y())
        )
        assert not viewer.comparisonDividerInteraction().hit_test(
            QPointF(midpoint.x() + 6.2, midpoint.y())
        )
    finally:
        _cleanup_qpane(viewer, qapp)


def test_vertical_divider_drag_updates_split_from_scene_coordinates(qapp) -> None:
    """Dragging a vertical divider should update split using scene coordinates."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        geometry = viewer.comparisonDividerInteraction().geometry()
        assert geometry is not None
        assert geometry.visible_segment is not None
        start = _segment_midpoint(geometry.visible_segment)
        target_split = 0.8
        target_scene_x = (
            geometry.scene_bounds.x + geometry.scene_bounds.width * target_split
        )
        target_source_x = (
            (target_scene_x - geometry.item.placement.x)
            * geometry.item.source_image.width()
            / geometry.item.placement.width
        )
        target = geometry.item.transform.map(QPointF(target_source_x, 50.0))

        viewer.mousePressEvent(_make_mouse_event(QEvent.Type.MouseButtonPress, start))
        assert viewer.comparisonDividerState().dragging is True
        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                target,
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        viewer.mouseReleaseEvent(
            _make_mouse_event(QEvent.Type.MouseButtonRelease, target)
        )

        assert viewer.comparisonDividerState().dragging is False
        assert viewer.comparisonState().orientation == ComparisonOrientation.VERTICAL
        assert viewer.comparisonState().split_position == pytest.approx(target_split)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_horizontal_divider_drag_updates_split_from_scene_coordinates(qapp) -> None:
    """Dragging a horizontal divider should update split using scene coordinates."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(
        qapp,
        orientation=ComparisonOrientation.HORIZONTAL,
    )
    try:
        geometry = viewer.comparisonDividerInteraction().geometry()
        assert geometry is not None
        assert geometry.visible_segment is not None
        start = _segment_midpoint(geometry.visible_segment)
        target_split = 0.25
        target_scene_y = (
            geometry.scene_bounds.y + geometry.scene_bounds.height * target_split
        )
        target_source_y = (
            (target_scene_y - geometry.item.placement.y)
            * geometry.item.source_image.height()
            / geometry.item.placement.height
        )
        target = geometry.item.transform.map(QPointF(50.0, target_source_y))

        viewer.mousePressEvent(_make_mouse_event(QEvent.Type.MouseButtonPress, start))
        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                target,
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        viewer.mouseReleaseEvent(
            _make_mouse_event(QEvent.Type.MouseButtonRelease, target)
        )

        assert viewer.comparisonState().orientation == ComparisonOrientation.HORIZONTAL
        assert viewer.comparisonState().split_position == pytest.approx(target_split)
    finally:
        _cleanup_qpane(viewer, qapp)


def test_pure_comparison_split_marks_bounded_dirty_region(qapp, monkeypatch) -> None:
    """Moving the split should dirty only the swept divider band."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        plan = viewer.view().calculateRenderPlan(is_blank=False)
        assert plan is not None
        viewer.view().renderer._current_render_plan = plan
        dirty_calls = []
        monkeypatch.setattr(viewer.view(), "mark_dirty", dirty_calls.append)

        viewer.setComparisonSplit(0.6, ComparisonOrientation.VERTICAL)

        assert len(dirty_calls) == 1
        dirty_rect = dirty_calls[0]
        assert dirty_rect is not None
        assert 0 < dirty_rect.width() < viewer.rect().width()
        assert dirty_rect.height() > 0
    finally:
        _cleanup_qpane(viewer, qapp)


def test_comparison_source_change_marks_full_dirty(qapp, monkeypatch) -> None:
    """Changing the comparison source should keep full dirty invalidation."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        next_id = uuid.uuid4()
        image_map = QPane.imageMapFromLists(
            [_solid_image(color=Qt.red), _solid_image(color=Qt.blue)],
            [Path("base.png"), Path("next-compare.png")],
            [viewer.currentImageID(), next_id],
        )
        viewer.setImagesByID(image_map, viewer.currentImageID())
        dirty_calls = []
        monkeypatch.setattr(viewer.view(), "mark_dirty", dirty_calls.append)

        viewer.setComparisonImageID(next_id)

        assert dirty_calls == [None]
    finally:
        _cleanup_qpane(viewer, qapp)


def test_divider_state_tracks_hover_and_cursor(qapp) -> None:
    """Hovering the divider should update public state and resize cursor."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        divider = viewer.comparisonDividerState()
        assert divider.visible_segment is not None
        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                _segment_midpoint(divider.visible_segment),
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )
        assert viewer.comparisonDividerState().hovered is True
        assert viewer.cursor().shape() == Qt.CursorShape.SizeHorCursor

        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                QPointF(10, 10),
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )
        assert viewer.comparisonDividerState().hovered is False
        assert viewer.cursor().shape() == Qt.CursorShape.ArrowCursor
    finally:
        _cleanup_qpane(viewer, qapp)


def test_disabled_divider_interaction_routes_to_active_tool(qapp) -> None:
    """Disabling divider interaction should let the active tool receive boundary events."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    events: list[str] = []

    class ProbeTool(ExtensionTool):
        """Record mouse events routed through the public tool surface."""

        def mousePressEvent(self, event: QMouseEvent) -> None:
            """Record a press event."""
            events.append("press")

    try:
        divider = viewer.comparisonDividerState()
        assert divider.visible_segment is not None
        viewer.registerTool("probe", ProbeTool)
        viewer.setControlMode("probe")
        viewer.setComparisonDividerInteractive(False)

        state = viewer.comparisonDividerState()
        assert state.enabled is True
        assert state.interactive is False
        viewer.mousePressEvent(
            _make_mouse_event(
                QEvent.Type.MouseButtonPress,
                _segment_midpoint(divider.visible_segment),
            )
        )

        assert events == ["press"]
        assert viewer.comparisonState().split_position == pytest.approx(0.5)
    finally:
        viewer.setControlMode(QPane.CONTROL_MODE_PANZOOM)
        viewer.unregisterTool("probe")
        _cleanup_qpane(viewer, qapp)


def test_non_boundary_drag_routes_to_active_tool(qapp) -> None:
    """The comparison divider should not steal normal tool drags away from the split."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    events: list[str] = []

    class ProbeTool(ExtensionTool):
        """Record mouse events routed through the public tool surface."""

        def mousePressEvent(self, event: QMouseEvent) -> None:
            """Record a press event."""
            events.append("press")

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            """Record a move event."""
            events.append("move")

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            """Record a release event."""
            events.append("release")

    try:
        viewer.registerTool("probe", ProbeTool)
        viewer.setControlMode("probe")

        viewer.mousePressEvent(
            _make_mouse_event(QEvent.Type.MouseButtonPress, QPointF(10, 10))
        )
        viewer.mouseMoveEvent(
            _make_mouse_event(
                QEvent.Type.MouseMove,
                QPointF(20, 10),
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        viewer.mouseReleaseEvent(
            _make_mouse_event(QEvent.Type.MouseButtonRelease, QPointF(20, 10))
        )

        assert events == ["press", "move", "release"]
        assert viewer.comparisonState().split_position == pytest.approx(0.5)
    finally:
        viewer.setControlMode(QPane.CONTROL_MODE_PANZOOM)
        viewer.unregisterTool("probe")
        _cleanup_qpane(viewer, qapp)


def test_host_overlay_can_draw_from_public_divider_state(qapp) -> None:
    """Host overlays should be able to draw a divider without private APIs."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    drawn_segments: list[QLineF] = []

    def draw_host_divider(painter, _state) -> None:
        divider = viewer.comparisonDividerState()
        assert divider.visible_segment is not None
        painter.drawLine(divider.visible_segment)
        drawn_segments.append(QLineF(divider.visible_segment))

    try:
        viewer.registerOverlay("host-compare-divider", draw_host_divider)
        viewer.paintEvent(None)

        assert drawn_segments
        _assert_vertical_segment_matches_rendered_transition(viewer)
    finally:
        viewer.unregisterOverlay("host-compare-divider")
        _cleanup_qpane(viewer, qapp)


def test_comparison_disabled_returns_disabled_divider_state(qapp) -> None:
    """Clearing comparison should deactivate public divider state and hit testing."""
    viewer, _base_id, _compare_id = _viewer_with_comparison(qapp)
    try:
        viewer.clearComparisonImage()

        divider = viewer.comparisonDividerState()
        assert divider.enabled is False
        assert divider.visible_segment is None
        assert divider.full_segment is None
        assert viewer.comparisonDividerInteraction().hit_test(QPointF(50, 50)) is False
        assert viewer.comparisonDividerInteraction().cursor() is None
    finally:
        _cleanup_qpane(viewer, qapp)


def test_removed_divider_style_api_is_absent_from_stub_and_demo() -> None:
    """Removed painted-divider APIs should not remain in public files or demo."""
    stub_text = (_PROJECT_ROOT / "qpane" / "qpane.pyi").read_text(encoding="utf-8")
    demo_text = (
        _PROJECT_ROOT / "examples" / "demonstration" / "demo_window.py"
    ).read_text(encoding="utf-8")
    demo_copy = (
        _PROJECT_ROOT / "examples" / "demonstration" / "demo_text.py"
    ).read_text(encoding="utf-8")
    qpane_text = (_PROJECT_ROOT / "qpane" / "qpane.py").read_text(encoding="utf-8")

    for removed in (
        "ComparisonDividerStyle",
        "comparisonDividerStyle",
        "setComparisonDividerStyle",
        "Show Compare Divider",
    ):
        assert removed not in stub_text
        assert removed not in demo_text
        assert removed not in demo_copy
    assert "comparisonDividerInteraction().draw_overlay" not in qpane_text
