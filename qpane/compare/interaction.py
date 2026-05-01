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

"""Mouse interaction and host-facing state for comparison dividers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QCursor, QMouseEvent

from ..rendering.clip_geometry import ProjectedClipBoundary
from ..rendering.clip_geometry import projected_comparison_boundary
from ..scene.render_plan import SceneRenderPlan
from ..types import ComparisonDividerState, ComparisonOrientation
from .service import CompareService

if TYPE_CHECKING:
    from ..qpane import QPane


class CompareDividerInteraction:
    """Own mouse-driven comparison divider behavior."""

    _HIT_WIDTH = 12.0

    def __init__(self, *, qpane: "QPane", service: CompareService) -> None:
        """Capture the owning widget and comparison state service."""
        self._qpane = qpane
        self._service = service
        self._interactive = True
        self._hovered = False
        self._dragging = False

    def interactive(self) -> bool:
        """Return whether divider mouse interaction is enabled."""
        return self._interactive

    def set_interactive(self, enabled: bool) -> None:
        """Enable or disable divider hit testing and dragging."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._interactive = enabled
        if not enabled:
            self.cancel_drag()

    def cancel_drag(self) -> None:
        """Clear active hover and drag state."""
        self._dragging = False
        self._hovered = False

    def cursor(self) -> QCursor | None:
        """Return the divider cursor when hover or drag state owns the pointer."""
        if not self._interaction_active() or not (self._hovered or self._dragging):
            return None
        orientation = self._service.state().orientation
        shape = (
            Qt.CursorShape.SizeVerCursor
            if orientation == ComparisonOrientation.HORIZONTAL
            else Qt.CursorShape.SizeHorCursor
        )
        return QCursor(shape)

    def geometry(self) -> ProjectedClipBoundary | None:
        """Return divider geometry for the active comparison state."""
        state = self._service.state()
        if not state.enabled:
            return None
        plan = self._current_render_plan()
        if plan is None:
            return None
        return projected_comparison_boundary(
            plan,
            orientation=state.orientation,
            hit_width=self._HIT_WIDTH,
        )

    def state(self) -> ComparisonDividerState:
        """Return public divider state for host-owned drawing."""
        comparison = self._service.state()
        geometry = self.geometry()
        if not comparison.enabled or geometry is None:
            return ComparisonDividerState(
                orientation=comparison.orientation,
                hit_width=self._HIT_WIDTH,
            )
        return ComparisonDividerState(
            enabled=True,
            interactive=self._interactive,
            hovered=self._hovered,
            dragging=self._dragging,
            orientation=geometry.orientation,
            hit_width=self._HIT_WIDTH,
            full_segment=geometry.full_segment,
            visible_segment=geometry.visible_segment,
        )

    def hit_test(self, point: QPointF) -> bool:
        """Return whether ``point`` is inside the divider hit target."""
        geometry = self.geometry()
        return geometry is not None and geometry.contains(point)

    def handle_mouse_press(self, event: QMouseEvent) -> bool:
        """Start divider dragging when a left press lands on the boundary."""
        if (
            event.button() != Qt.MouseButton.LeftButton
            or not self._interaction_active()
            or not self.hit_test(event.position())
        ):
            return False
        self._dragging = True
        self._hovered = True
        self._set_split_from_point(event.position())
        event.accept()
        self._qpane.update()
        return True

    def handle_mouse_move(self, event: QMouseEvent) -> bool:
        """Update hover state or drag the divider from a mouse move event."""
        if not self._interaction_active():
            changed = self._hovered or self._dragging
            self.cancel_drag()
            if changed:
                self._qpane.update()
            return False
        if self._dragging:
            self._set_split_from_point(event.position())
            event.accept()
            self._qpane.update()
            return True
        hovered = self.hit_test(event.position())
        if hovered != self._hovered:
            self._hovered = hovered
            self._qpane.update()
        return False

    def handle_mouse_release(self, event: QMouseEvent) -> bool:
        """End divider dragging on left-button release."""
        if not self._dragging or event.button() != Qt.MouseButton.LeftButton:
            return False
        self._dragging = False
        self._hovered = self.hit_test(event.position())
        event.accept()
        self._qpane.update()
        return True

    def _interaction_active(self) -> bool:
        """Return whether divider hit testing should run."""
        return self._interactive and self._service.state().enabled

    def _set_split_from_point(self, point: QPointF) -> None:
        """Convert a widget point into a normalized comparison split."""
        geometry = self.geometry()
        if geometry is None:
            return
        raw = geometry.split_for_widget_point(point)
        if raw is None:
            return
        self._service.set_split(
            raw,
            geometry.orientation,
        )

    def _current_render_plan(self) -> SceneRenderPlan | None:
        """Return the latest render plan, calculating one only when needed."""
        try:
            renderer = self._qpane.presenter().renderer
            plan = renderer.get_current_render_plan()
        except AttributeError:
            plan = None
        if plan is not None:
            return plan
        try:
            return self._qpane.view().calculateRenderPlan(is_blank=False)
        except AttributeError:
            return None
