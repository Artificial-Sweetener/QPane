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

"""Tests for pan/zoom tool interactions."""

from __future__ import annotations
from typing import List, Tuple
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from qpane.rendering import ViewportZoomMode
from qpane.tools import PanZoomTool, ToolDependencies


class _PositioningMouseEvent:
    def __init__(
        self,
        point: QPointF,
        *,
        button: Qt.MouseButton = Qt.MouseButton.LeftButton,
        buttons: Qt.MouseButton | None = None,
    ):
        self._point = QPointF(point)
        self._button = button
        self._buttons = buttons if buttons is not None else button

    def button(self) -> Qt.MouseButton:
        return self._button

    def buttons(self) -> Qt.MouseButton:
        return self._buttons

    def position(self) -> QPointF:
        return QPointF(self._point)

    def set_position(self, point: QPointF) -> None:
        self._point = QPointF(point)


class _WheelEventStub:
    def __init__(self, point: QPointF, delta_y: int):
        self._point = QPointF(point)
        self._delta_y = delta_y
        self.accepted = False

    def position(self) -> QPointF:
        return QPointF(self._point)

    def angleDelta(self) -> QPoint:
        return QPoint(0, self._delta_y)

    def accept(self) -> None:
        self.accepted = True


def _make_mouse_event(
    event_type: QEvent.Type,
    point: QPointF,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton | None = None,
) -> QMouseEvent:
    buttons = buttons if buttons is not None else button
    return QMouseEvent(
        event_type,
        point,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
        Qt.MouseEventSource.MouseEventNotSynthesized,
    )


@pytest.fixture(autouse=True)
def _patch_drag_distance(monkeypatch):
    class _DummyApp:
        def startDragDistance(self) -> int:
            return 5

    monkeypatch.setattr(QApplication, "instance", lambda: _DummyApp())
    yield


def test_panzoom_respects_lock(qapp):
    tool = PanZoomTool()
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: True,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    received: List[str] = []
    tool.signals.pan_requested.connect(lambda *_: received.append("pan"))
    tool.signals.zoom_requested.connect(lambda *_: received.append("zoom"))
    press = _PositioningMouseEvent(QPointF(10, 10))
    tool.mousePressEvent(press)
    move = _PositioningMouseEvent(QPointF(20, 20), buttons=Qt.MouseButton.LeftButton)
    tool.mouseMoveEvent(move)
    wheel = _WheelEventStub(QPointF(10, 10), 120)
    tool.wheelEvent(wheel)
    assert received == []


def test_panzoom_emits_pan_when_dragging(qapp):
    tool = PanZoomTool()
    current_pan = QPointF(4.0, 8.0)
    emissions: List[QPointF] = []
    tool.signals.pan_requested.connect(lambda pan: emissions.append(pan))
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: True,
            get_pan=lambda: current_pan,
            get_zoom=lambda: 2.0,
        )
    )
    press = _PositioningMouseEvent(QPointF(1, 1))
    tool.mousePressEvent(press)
    move = _PositioningMouseEvent(QPointF(6, 9), buttons=Qt.MouseButton.LeftButton)
    tool.mouseMoveEvent(move)
    assert emissions == [QPointF(9.0, 16.0)]


def test_panzoom_does_not_emit_cursor_update_for_steady_pan_move(qapp):
    tool = PanZoomTool()
    current_pan = QPointF(4.0, 8.0)
    pan_emissions: list[QPointF] = []
    cursor_emissions: list[None] = []
    tool.signals.pan_requested.connect(lambda pan: pan_emissions.append(pan))
    tool.signals.cursor_update_requested.connect(lambda: cursor_emissions.append(None))
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: True,
            get_pan=lambda: current_pan,
            get_zoom=lambda: 2.0,
        )
    )
    press = _PositioningMouseEvent(QPointF(1, 1))
    tool.mousePressEvent(press)
    assert len(cursor_emissions) == 1
    cursor_emissions.clear()
    move = _PositioningMouseEvent(QPointF(6, 9), buttons=Qt.MouseButton.LeftButton)
    tool.mouseMoveEvent(move)
    assert pan_emissions == [QPointF(9.0, 16.0)]
    assert cursor_emissions == []
    release = _PositioningMouseEvent(QPointF(6, 9))
    tool.mouseReleaseEvent(release)
    assert len(cursor_emissions) == 1


def test_panzoom_scales_drag_delta_by_dpr(qapp):
    tool = PanZoomTool()
    current_pan = QPointF(10.0, 20.0)
    emissions: List[QPointF] = []
    tool.signals.pan_requested.connect(lambda pan: emissions.append(pan))
    dpr = 1.25
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: True,
            get_pan=lambda: current_pan,
            get_zoom=lambda: 2.0,
            get_dpr=lambda: dpr,
        )
    )
    press = _PositioningMouseEvent(QPointF(0, 0))
    tool.mousePressEvent(press)
    move = _PositioningMouseEvent(QPointF(8, -4), buttons=Qt.MouseButton.LeftButton)
    tool.mouseMoveEvent(move)
    assert len(emissions) == 1
    emitted = emissions[0]
    assert emitted.x() == pytest.approx(current_pan.x() + 8 * dpr)
    assert emitted.y() == pytest.approx(current_pan.y() - 4 * dpr)


def test_panzoom_drag_out_threshold(qapp):
    tool = PanZoomTool()
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: True,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    press = _make_mouse_event(QEvent.Type.MouseButtonPress, QPointF(5, 5))
    tool.mousePressEvent(press)
    close_move = _make_mouse_event(
        QEvent.Type.MouseMove,
        QPointF(6, 6),
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    tool.mouseMoveEvent(close_move)
    assert tool.drag_start_pos is not None
    far_move = _make_mouse_event(
        QEvent.Type.MouseMove,
        QPointF(11, 5),
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    tool.mouseMoveEvent(far_move)
    assert tool.drag_start_pos is None


def test_panzoom_cursor_transitions(qapp):
    tool = PanZoomTool()
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: True,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    assert tool.getCursor().shape() == Qt.CursorShape.OpenHandCursor
    press = _PositioningMouseEvent(QPointF(0, 0))
    tool.mousePressEvent(press)
    assert tool.getCursor().shape() == Qt.CursorShape.ClosedHandCursor
    release = _PositioningMouseEvent(QPointF(0, 0))
    tool.mouseReleaseEvent(release)
    assert tool.getCursor().shape() == Qt.CursorShape.OpenHandCursor
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: True,
            can_pan=lambda: True,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    assert tool.getCursor().shape() == Qt.CursorShape.ArrowCursor


def test_panzoom_cursor_stays_arrow_when_pan_locked(qapp):
    tool = PanZoomTool()
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: True,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    assert tool.getCursor().shape() == Qt.CursorShape.ArrowCursor


def test_panzoom_cursor_arrow_when_pan_not_possible(qapp):
    tool = PanZoomTool()
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    assert tool.getCursor().shape() == Qt.CursorShape.ArrowCursor


def test_panzoom_does_not_enter_panning_when_pan_impossible(qapp):
    tool = PanZoomTool()
    emissions: List[QPointF] = []
    tool.signals.pan_requested.connect(lambda pan: emissions.append(pan))
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            is_drag_out_allowed=lambda: False,
            can_pan=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
        )
    )
    press = _PositioningMouseEvent(QPointF(0, 0))
    tool.mousePressEvent(press)
    move = _PositioningMouseEvent(QPointF(5, 5), buttons=Qt.MouseButton.LeftButton)
    tool.mouseMoveEvent(move)
    release = _PositioningMouseEvent(QPointF(5, 5))
    tool.mouseReleaseEvent(release)
    assert emissions == []
    assert tool.getCursor().shape() == Qt.CursorShape.ArrowCursor


def test_panzoom_wheel_emits_zoom(qapp):
    tool = PanZoomTool()
    zooms: List[Tuple[float, QPoint]] = []
    current_zoom = 2.0
    native_zoom = 1.0

    def on_zoom(value: float, anchor: QPoint) -> None:
        nonlocal current_zoom
        zooms.append((value, anchor))
        current_zoom = value

    tool.signals.zoom_requested.connect(on_zoom)
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: current_zoom,
            get_native_zoom=lambda: native_zoom,
        )
    )
    grow_event = _WheelEventStub(QPointF(5, 5), 120)
    tool.wheelEvent(grow_event)
    shrink_event = _WheelEventStub(QPointF(5, 5), -120)
    tool.wheelEvent(shrink_event)
    assert zooms[0][0] == pytest.approx(2.5)
    assert zooms[0][1] == QPoint(5, 5)
    assert zooms[1][0] == pytest.approx(2.0)


def test_panzoom_wheel_uses_delta_magnitude(qapp):
    tool = PanZoomTool()
    zooms: List[float] = []
    current_zoom = 2.0

    def on_zoom(value: float, _anchor: QPoint) -> None:
        nonlocal current_zoom
        zooms.append(value)
        current_zoom = value

    tool.signals.zoom_requested.connect(on_zoom)
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: current_zoom,
            get_native_zoom=lambda: 1.0,
        )
    )
    grow_event = _WheelEventStub(QPointF(5, 5), 240)
    tool.wheelEvent(grow_event)
    shrink_event = _WheelEventStub(QPointF(5, 5), -240)
    tool.wheelEvent(shrink_event)
    assert zooms[0] == pytest.approx(2.0 * 1.25 * 1.25)
    assert zooms[1] == pytest.approx(2.0)
    assert grow_event.accepted is True
    assert shrink_event.accepted is True


def test_panzoom_wheel_snaps_to_native_zoom_on_crossing(qapp):
    tool = PanZoomTool()
    emissions: List[Tuple[float, ViewportZoomMode]] = []
    current_zoom = 0.9
    native_zoom = 1.0

    def on_snap(value: float, _anchor: QPoint, mode: ViewportZoomMode) -> None:
        nonlocal current_zoom
        emissions.append((value, mode))
        current_zoom = value

    tool.signals.zoom_snap_requested.connect(on_snap)
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: current_zoom,
            get_native_zoom=lambda: native_zoom,
        )
    )
    tool.wheelEvent(_WheelEventStub(QPointF(5, 5), 120))
    assert emissions == [(native_zoom, ViewportZoomMode.ONE_TO_ONE)]


def test_panzoom_wheel_snaps_to_native_zoom_on_reverse_crossing(qapp):
    tool = PanZoomTool()
    emissions: List[Tuple[float, ViewportZoomMode]] = []
    current_zoom = 1.2
    native_zoom = 1.0

    def on_snap(value: float, _anchor: QPoint, mode: ViewportZoomMode) -> None:
        nonlocal current_zoom
        emissions.append((value, mode))
        current_zoom = value

    tool.signals.zoom_snap_requested.connect(on_snap)
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: current_zoom,
            get_native_zoom=lambda: native_zoom,
        )
    )
    tool.wheelEvent(_WheelEventStub(QPointF(5, 5), -120))
    assert emissions == [(native_zoom, ViewportZoomMode.ONE_TO_ONE)]


def test_panzoom_wheel_snaps_to_hidpi_native_zoom(qapp):
    tool = PanZoomTool()
    emissions: List[Tuple[float, ViewportZoomMode]] = []
    current_zoom = 1.4
    native_zoom = 1.5

    def on_snap(value: float, _anchor: QPoint, mode: ViewportZoomMode) -> None:
        nonlocal current_zoom
        emissions.append((value, mode))
        current_zoom = value

    tool.signals.zoom_snap_requested.connect(on_snap)
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: current_zoom,
            get_native_zoom=lambda: native_zoom,
        )
    )
    tool.wheelEvent(_WheelEventStub(QPointF(5, 5), 120))
    assert emissions == [(native_zoom, ViewportZoomMode.ONE_TO_ONE)]


def test_panzoom_double_click_sets_zoom_fit_when_not_fit(qapp):
    tool = PanZoomTool()
    calls = []
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
            get_zoom_mode=lambda: ViewportZoomMode.CUSTOM,
            set_zoom_fit=lambda: calls.append("fit"),
            set_zoom_one_to_one=lambda anchor=None: calls.append(("one", anchor)),
        )
    )
    event = _make_mouse_event(QEvent.Type.MouseButtonDblClick, QPointF(9, 7))
    tool.mouseDoubleClickEvent(event)
    assert calls == ["fit"]


def test_panzoom_double_click_sets_zoom_one_to_one_when_fit(qapp):
    tool = PanZoomTool()
    calls: List[object] = []

    def record_one_to_one(anchor=None):
        calls.append(("one", anchor))

    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: False,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
            get_zoom_mode=lambda: ViewportZoomMode.FIT,
            set_zoom_fit=lambda: calls.append("fit"),
            set_zoom_one_to_one=record_one_to_one,
        )
    )
    event = _make_mouse_event(QEvent.Type.MouseButtonDblClick, QPointF(12, 15))
    tool.mouseDoubleClickEvent(event)
    assert calls == [("one", QPoint(12, 15))]


def test_panzoom_double_click_ignored_when_inactive_conditions(qapp):
    tool = PanZoomTool()
    calls = []
    deps = ToolDependencies(
        is_pan_zoom_locked=lambda: True,
        is_image_null=lambda: False,
        get_pan=lambda: QPointF(0, 0),
        get_zoom=lambda: 1.0,
        get_zoom_mode=lambda: ViewportZoomMode.CUSTOM,
        set_zoom_fit=lambda: calls.append("fit"),
        set_zoom_one_to_one=lambda anchor=None: calls.append(("one", anchor)),
    )
    tool.activate(deps)
    locked_event = _make_mouse_event(QEvent.Type.MouseButtonDblClick, QPointF(1, 1))
    tool.mouseDoubleClickEvent(locked_event)
    assert calls == []
    tool.activate(
        ToolDependencies(
            is_pan_zoom_locked=lambda: False,
            is_image_null=lambda: True,
            get_pan=lambda: QPointF(0, 0),
            get_zoom=lambda: 1.0,
            get_zoom_mode=lambda: ViewportZoomMode.CUSTOM,
            set_zoom_fit=lambda: calls.append("fit"),
            set_zoom_one_to_one=lambda anchor=None: calls.append(("one", anchor)),
        )
    )
    blank_event = _make_mouse_event(QEvent.Type.MouseButtonDblClick, QPointF(2, 2))
    tool.mouseDoubleClickEvent(blank_event)
    assert calls == []
