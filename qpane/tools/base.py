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

"""Core tool abstractions plus the default pan/zoom implementation."""

import abc
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QCursor,
    QEnterEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication

from .dependencies import ToolDependencies

if TYPE_CHECKING:
    pass


def _viewport_zoom_mode():
    """Lazily import the ViewportZoomMode enum to avoid heavy import cycles."""
    from ..rendering import ViewportZoomMode

    return ViewportZoomMode


class ExtensionToolSignals(QObject):
    """Public signal hub for extension tools to request viewer actions."""

    pan_requested = Signal(QPointF)
    zoom_requested = Signal(float, QPointF)
    repaint_overlay_requested = Signal()
    cursor_update_requested = Signal()


class ExtensionTool(abc.ABC):
    """Public interface for extension tools that handle input and overlays."""

    signals: ExtensionToolSignals

    def __init__(self) -> None:
        """Create the shared ExtensionToolSignals instance available to subclasses."""
        self.signals = ExtensionToolSignals()

    def activate(self, dependencies: ToolDependencies) -> None:
        """Inject collaborators when a tool becomes active; base implementation does nothing."""
        return None

    def deactivate(self) -> None:
        """Hook for cleanup when a tool is deactivated; base implementation does nothing."""
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Ignore presses by default so tools opt in explicitly."""
        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Ignore moves by default; subclasses override to react."""
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Ignore releases by default to keep the base inert."""
        event.ignore()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Ignore double-clicks by default; tools opt in when they care."""
        event.ignore()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Ignore wheel gestures by default so scrolling stays inert."""
        event.ignore()

    def enterEvent(self, event: QEnterEvent) -> None:
        """Ignore enter events by default so the QPane maintains cursor state."""
        event.ignore()

    def leaveEvent(self, event: QEvent) -> None:
        """Ignore leave events by default so the QPane maintains cursor state."""
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Ignore key presses by default; tools override when they consume keys."""
        event.ignore()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Ignore key releases by default; tools override when they consume keys."""
        event.ignore()

    def draw_overlay(self, painter: QPainter) -> None:
        """No-op overlay pass; override to draw custom visuals."""
        return None

    def getCursor(self) -> QCursor | None:
        """Return the cursor the QPane should adopt when this tool is active.

        Return ``None`` to defer cursor selection to registered cursor providers.
        """
        return QCursor(Qt.CursorShape.ArrowCursor)


class ToolSignals(ExtensionToolSignals):
    """Internal signal hub for built-in tools and feature wiring."""

    drag_start_maybe_requested = Signal(QMouseEvent)
    stroke_applied = Signal(QPoint, QPoint, bool)
    stroke_completed = Signal()
    brush_size_changed = Signal(int)
    region_selected_for_masking = Signal(np.ndarray, bool)
    mask_component_adjustment_requested = Signal(QPoint, bool)
    undo_state_push_requested = Signal()
    zoom_snap_requested = Signal(float, QPointF, object)


class BaseTool(ExtensionTool):
    """Internal interface for built-in QPane tools."""

    signals: ToolSignals

    def __init__(self) -> None:
        """Create the internal ToolSignals instance available to subclasses."""
        self.signals = ToolSignals()

    @staticmethod
    def _maybe_trigger_drag_out(
        event: QMouseEvent,
        drag_start_pos: QPoint | None,
        is_drag_out_allowed,
    ) -> tuple[QPoint | None, bool]:
        """Detect drag-out threshold and report whether to emit drag-start."""
        if drag_start_pos is None:
            return None, False
        if not is_drag_out_allowed():
            return drag_start_pos, False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return drag_start_pos, False
        pos = event.position().toPoint()
        distance = (pos - drag_start_pos).manhattanLength()
        if distance >= QApplication.instance().startDragDistance():
            return None, True
        return drag_start_pos, False


class CursorTool(BaseTool):
    """Tool that leaves interactions inert while keeping the default cursor."""

    def __init__(self):
        """Initialize state used for drag-out detection."""
        super().__init__()
        self._reset_state()

    def _reset_state(self) -> None:
        """Clear cached guards and pointer tracking."""
        self._is_drag_out_allowed = lambda: False
        self._is_image_null = lambda: True
        self._drag_start_pos: QPoint | None = None

    def activate(self, dependencies: ToolDependencies) -> None:
        """Capture drag-out guards while staying otherwise inert."""
        self._is_drag_out_allowed = dependencies.get(
            "is_drag_out_allowed", lambda: False
        )
        self._is_image_null = dependencies.get("is_image_null", lambda: True)

    def deactivate(self):
        """Clear cached drag-out state."""
        self._reset_state()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Track left-button presses to allow drag-out when permitted."""
        if event.button() == Qt.MouseButton.LeftButton and not self._is_image_null():
            self._drag_start_pos = event.position().toPoint()
        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Promote left drags to drag-out; otherwise stay inert."""
        self._drag_start_pos, should_emit = self._maybe_trigger_drag_out(
            event, self._drag_start_pos, self._is_drag_out_allowed
        )
        if should_emit:
            self.signals.drag_start_maybe_requested.emit(event)
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Reset drag tracking and stay inert on release."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None
        event.ignore()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Ignore wheel gestures so content is unaffected."""
        if hasattr(event, "ignore"):
            event.ignore()


class PanZoomTool(BaseTool):
    """Default tool that handles drag panning, wheel zooming, and fit toggling."""

    _WHEEL_UNITS_PER_STEP = 120.0
    _WHEEL_ZOOM_IN_FACTOR = 1.25
    _WHEEL_ZOOM_OUT_FACTOR = 0.8

    def __init__(self):
        """Prepare pan/zoom state and dependency defaults."""
        super().__init__()
        self._reset_state()

    def _reset_state(self) -> None:
        """Restore runtime state and dependency hooks to their defaults."""
        self.panning = False
        self.drag_start_pos = None
        self.last_mouse_pos = None
        self._is_pan_zoom_locked = lambda: True
        self._is_image_null = lambda: True
        self._is_drag_out_allowed = lambda: False
        self._can_pan = lambda: False
        self._get_pan = lambda: QPointF(0, 0)
        self._get_zoom = lambda: 1.0
        self._get_native_zoom = lambda: 1.0
        self._get_fit_zoom = lambda: 1.0
        self._get_zoom_mode = lambda: _viewport_zoom_mode().FIT
        self._set_zoom_fit = lambda: None
        self._set_zoom_fit_interpolated = None
        self._set_zoom_one_to_one = lambda anchor=None: None
        self._set_zoom_one_to_one_interpolated = None
        self._get_dpr = lambda: 1.0

    def activate(self, dependencies: ToolDependencies) -> None:
        """Wire viewport pan and zoom hooks supplied by the host.

        Args:
            dependencies: Callables describing lock state, pan/zoom accessors,
                drag-out toggles, and zoom-mode setters. Missing entries fall
                back to safe defaults that keep interactions disabled when
                content is absent.
        """
        self._is_pan_zoom_locked = dependencies.get("is_pan_zoom_locked", lambda: True)
        self._is_image_null = dependencies.get("is_image_null", lambda: True)
        self._is_drag_out_allowed = dependencies.get(
            "is_drag_out_allowed", lambda: False
        )
        self._can_pan = dependencies.get("can_pan", lambda: False)
        self._get_pan = dependencies.get("get_pan", lambda: QPointF(0, 0))
        self._get_zoom = dependencies.get("get_zoom", lambda: 1.0)
        self._get_native_zoom = dependencies.get("get_native_zoom", lambda: 1.0)
        self._get_fit_zoom = dependencies.get("get_fit_zoom", lambda: 1.0)
        self._get_zoom_mode = dependencies.get(
            "get_zoom_mode", lambda: _viewport_zoom_mode().FIT
        )
        self._set_zoom_fit = dependencies.get("set_zoom_fit", lambda: None)
        self._set_zoom_fit_interpolated = dependencies.get("set_zoom_fit_interpolated")
        self._set_zoom_one_to_one = dependencies.get(
            "set_zoom_one_to_one", lambda anchor=None: None
        )
        self._set_zoom_one_to_one_interpolated = dependencies.get(
            "set_zoom_one_to_one_interpolated"
        )
        self._get_dpr = dependencies.get("get_dpr", lambda: 1.0)

    def deactivate(self):
        """Reset captured hooks to defaults to avoid stale references."""
        self._reset_state()

    def getCursor(self) -> QCursor:
        """Show hand cursors only while panning or when panning is possible and drag-out is disabled."""
        if self.panning:
            return QCursor(Qt.CursorShape.ClosedHandCursor)
        if (
            not self._is_image_null()
            and self._can_pan()
            and not self._is_drag_out_allowed()
        ):
            return QCursor(Qt.CursorShape.OpenHandCursor)
        return QCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Track left-button presses when interactions are unlocked, entering panning unless drag-out is enabled."""
        if self._is_pan_zoom_locked():
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._is_image_null():
            self.drag_start_pos = event.position().toPoint()
            self.last_mouse_pos = event.position().toPoint()
            if not self._is_drag_out_allowed() and self._can_pan():
                self.panning = True
                self.signals.cursor_update_requested.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Promote left-button drags to drag-out after the threshold or emit pan requests while dragging."""
        if self._is_pan_zoom_locked():
            return
        self.drag_start_pos, should_emit = self._maybe_trigger_drag_out(
            event, self.drag_start_pos, self._is_drag_out_allowed
        )
        if should_emit:
            self.signals.drag_start_maybe_requested.emit(event)
            return
        was_panning = self.panning
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self.last_mouse_pos is not None
            and not self._is_drag_out_allowed()
            and self._can_pan()
        ):
            delta = event.position() - self.last_mouse_pos
            new_pan = self._get_pan() + self._logical_delta_to_physical(delta)
            self.signals.pan_requested.emit(new_pan)
        self.last_mouse_pos = event.position()
        if was_panning != self.panning:
            self.signals.cursor_update_requested.emit()

    def _logical_delta_to_physical(self, delta: QPointF) -> QPointF:
        """Scale a logical-pixel delta by DPR so pan stays in physical pixels."""
        try:
            dpr = float(self._get_dpr())
        except (
            Exception
        ):  # pragma: no cover - dependency should be safe but guard anyway
            dpr = 1.0
        if dpr <= 0:
            dpr = 1.0
        return QPointF(delta.x() * dpr, delta.y() * dpr)

    def _snap_zoom(
        self, old_zoom: float, new_zoom: float
    ) -> tuple[float, object | None]:
        """Snap wheel zoom to native or fit scale when a step crosses them."""
        try:
            native_zoom = float(self._get_native_zoom())
            fit_zoom = float(self._get_fit_zoom())
        except Exception:  # pragma: no cover - dependency should be safe
            return new_zoom, None
        zoom_mode = _viewport_zoom_mode()

        # Helper to check crossing
        def crosses(target: float) -> bool:
            """Return True if the zoom transition crosses the target value."""
            if target <= 0:
                return False
            if old_zoom < target <= new_zoom:
                return True
            if old_zoom > target >= new_zoom:
                return True
            return False

        crosses_native = crosses(native_zoom)
        crosses_fit = crosses(fit_zoom)

        if crosses_native and crosses_fit:
            # If we cross both, prioritize the one we weren't already at.
            # If we were at neither, prioritize native (1:1) as the "hard" target.
            # But practically, if they are this close, 1:1 is usually the user intent.
            return native_zoom, zoom_mode.ONE_TO_ONE

        if crosses_native:
            return native_zoom, zoom_mode.ONE_TO_ONE

        if crosses_fit:
            return fit_zoom, zoom_mode.FIT

        return new_zoom, None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End panning sessions and clear transient drag state."""
        if self._is_pan_zoom_locked():
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.panning = False
            self.signals.cursor_update_requested.emit()
        self.last_mouse_pos = None
        self.drag_start_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Toggle between fit and one-to-one zoom anchored at the click position."""
        if self._is_pan_zoom_locked():
            return
        if self._is_image_null():
            return
        if self._get_zoom_mode() != _viewport_zoom_mode().FIT:
            if self._set_zoom_fit_interpolated is not None:
                self._set_zoom_fit_interpolated()
            else:
                self._set_zoom_fit()
            return
        if self._set_zoom_one_to_one_interpolated is not None:
            self._set_zoom_one_to_one_interpolated(event.position())
        else:
            self._set_zoom_one_to_one(event.position())

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Request zoom adjustments around the pointer using wheel delta magnitude.

        Snaps to native 1:1 or fit zoom when a wheel step crosses them.
        """
        if self._is_pan_zoom_locked():
            return
        if self._is_image_null():
            return
        angle = event.angleDelta().y()
        if angle == 0:
            return
        wheel_steps = abs(angle) / self._WHEEL_UNITS_PER_STEP
        step_factor = (
            self._WHEEL_ZOOM_IN_FACTOR if angle > 0 else self._WHEEL_ZOOM_OUT_FACTOR
        )
        factor = step_factor**wheel_steps
        anchor = event.position()
        old_zoom = self._get_zoom()
        new_zoom, snap_mode = self._snap_zoom(old_zoom, old_zoom * factor)
        if snap_mode is not None:
            self.signals.zoom_snap_requested.emit(new_zoom, anchor, snap_mode)
            if hasattr(event, "accept"):
                event.accept()
            return
        self.signals.zoom_requested.emit(new_zoom, anchor)
        if hasattr(event, "accept"):
            event.accept()
