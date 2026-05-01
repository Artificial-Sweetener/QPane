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

"""Viewport state management for QPane's rendering surface."""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PySide6.QtCore import (
    QElapsedTimer,
    QObject,
    QPoint,
    QPointF,
    QSize,
    QSizeF,
    QTimer,
    Signal,
)
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QWidget

from ..core import Config
from .coordinates import CoordinateContext, PanelHitTest
from ..scene.render_plan import SceneContentSnapshot


class ViewportZoomMode(str, Enum):
    """Canonical zoom modes for the viewport."""

    FIT = "fit"
    ONE_TO_ONE = "1to1"
    CUSTOM = "custom"


@dataclass(frozen=True)
class SmoothZoomDiagnostics:
    """Snapshot of smooth zoom timing details for diagnostics overlays."""

    fps_hz: float
    using_fallback_fps: bool
    frame_ms: float
    mode: str


class Viewport(QObject):
    """Track pan/zoom state and coordinate transforms for the qpane viewport."""

    viewChanged = Signal()

    _SMOOTH_ZOOM_MIN_DELTA = 1e-4
    _SMOOTH_ZOOM_FAST_THRESHOLD_MS = 40.0
    _SMOOTH_ZOOM_SLOW_THRESHOLD_MS = 160.0
    _PAN_EPSILON_PX = 1e-6

    def __init__(self, qpane: QWidget, config: Config):
        """Initialise viewport state tied to the hosting qpane.

        Args:
            qpane: Widget hosting the viewport.
            config: Configuration snapshot controlling zoom and expansion limits.
        """
        super().__init__()
        self.qpane = qpane
        self._config = config
        self.content_size = QSize()  # Dimensions of the content (e.g., the image)
        self.zoom = 1.0
        self.fit_zoom = 1.0
        self.zoom_mode: ViewportZoomMode = ViewportZoomMode.FIT
        self.pan = QPointF(0, 0)
        self._pan_zoom_locked = False
        self._zoom_anim_timer = QTimer(self)
        self._zoom_anim_timer.timeout.connect(self._tick_zoom_animation)
        self._zoom_anim_elapsed = QElapsedTimer()
        self._zoom_anim_start_zoom = 1.0
        self._zoom_anim_target_zoom = 1.0
        self._refresh_smooth_zoom_settings()
        self._zoom_anim_duration_ms = self._smooth_zoom_duration_ms
        self._detected_refresh_rate: float | None = None
        self._zoom_anim_min_frame_ms = self._get_zoom_target_frame_ms()
        self._zoom_anim_timer.setInterval(self._zoom_anim_min_frame_ms)
        self._zoom_anim_start_pan = QPointF(0, 0)
        self._zoom_anim_target_pan: QPointF | None = None
        self._zoom_anim_anchor_physical: QPointF | None = None
        self._zoom_anim_panel_center = QPointF(0, 0)
        self._zoom_anim_pending = False
        self._zoom_anim_last_tick_ms: float | None = None
        self._zoom_last_request_time: float | None = None
        self._smooth_zoom_last_burst = False

    def setContentSize(self, size: QSize):
        """Record the content size used for zoom and clamp calculations."""
        self.content_size = size
        if size.isNull():
            self._stop_zoom_animation()

    def is_locked(self) -> bool:
        """Return whether pan and zoom updates are currently blocked."""
        return self._pan_zoom_locked

    def set_locked(self, locked: bool):
        """Enable or disable pan/zoom updates."""
        self._pan_zoom_locked = locked
        if locked:
            self._stop_zoom_animation()

    def get_zoom_mode(self) -> ViewportZoomMode:
        """Expose the active zoom mode."""
        return self.zoom_mode

    def setPan(self, pan: QPointF):
        """Clamp and apply panning when unlocked, emitting viewChanged on updates."""
        if self._pan_zoom_locked and pan != self.pan:
            return
        # _commit_zoom_change handles the check for whether the view changed
        # and emits the signal. We pass self.zoom because pan is independent of zoom.
        self._commit_zoom_change(self.zoom, pan)

    def setZoomAndPan(self, zoom: float, pan: QPointF) -> None:
        """Apply zoom and pan together when unlocked so callers move atomically."""
        if self._pan_zoom_locked and (zoom != self.zoom or pan != self.pan):
            return
        self._commit_zoom_change(zoom, pan)

    def nativeZoom(self) -> float:
        """Return the zoom level where one image pixel maps to one device pixel."""
        return 1.0  # Fallback if screen is not available

    def computeFitZoom(self) -> float:
        """Calculate the zoom level required to fit the content within the viewport."""
        if self.content_size.isNull():
            return 1.0
        viewport_phys = self.qpane.physicalViewportRect()
        panel_w = viewport_phys.width()
        panel_h = viewport_phys.height()
        img_w, img_h = self.content_size.width(), self.content_size.height()
        if img_w == 0 or img_h == 0:
            return 1.0
        return min(panel_w / img_w, panel_h / img_h)

    def min_zoom(self) -> float:
        """Compute the minimum zoom that keeps the longest side above the configured minimum size."""
        if self.content_size.isNull():
            return 0.01  # A small fallback value
        img_w = self.content_size.width()
        img_h = self.content_size.height()
        if img_w == 0 or img_h == 0:
            return 0.01  # Avoid division by zero
        longest_side = max(img_w, img_h)
        return self._config.min_view_size_px / longest_side

    def clampPan(
        self, pan: QPointF, zoom: float, panel_size, image_size: QSize
    ) -> QPointF:
        """Clamp pan so the scaled image stays within the viewport plus the expansion margin."""
        panel_w = float(panel_size.width())
        panel_h = float(panel_size.height())
        img_w = float(image_size.width() * zoom)
        img_h = float(image_size.height() * zoom)
        # The expansion factor > 1.0 allows panning beyond edge
        expansion = self._config.canvas_expansion_factor
        extra_x = (panel_w * (expansion - 1.0)) / 2
        extra_y = (panel_h * (expansion - 1.0)) / 2
        if self._axis_fits(img_w, panel_w):
            x = 0
        else:
            x_max = (img_w - panel_w) / 2 + extra_x
            x = min(max(pan.x(), -x_max), x_max)
        if self._axis_fits(img_h, panel_h):
            y = 0
        else:
            y_max = (img_h - panel_h) / 2 + extra_y
            y = min(max(pan.y(), -y_max), y_max)
        return QPointF(x, y)

    def applyConfig(self, config: Config) -> None:
        """Swap in a new configuration snapshot for zoom and clamp calculations."""
        self._config = config
        self._refresh_smooth_zoom_settings()
        self._zoom_anim_min_frame_ms = self._get_zoom_target_frame_ms()
        self._zoom_anim_timer.setInterval(self._zoom_anim_min_frame_ms)
        self._mark_diagnostics_dirty()

    def can_pan(
        self,
        *,
        zoom: Optional[float] = None,
        panel_size=None,
        image_size: Optional[QSize] = None,
    ) -> bool:
        """Return True when panning could change the viewport based on size and lock state."""
        if self._pan_zoom_locked:
            return False
        if self.zoom_mode == ViewportZoomMode.FIT:
            return False
        image_size = self.content_size if image_size is None else image_size
        panel_size = (
            self._physical_viewport_size() if panel_size is None else panel_size
        )
        if image_size is None or image_size.isNull():
            return False
        if panel_size is None or panel_size.isEmpty():
            return False
        zoom = self.zoom if zoom is None else zoom
        if zoom <= 0:
            return False
        img_w = float(image_size.width() * zoom)
        img_h = float(image_size.height() * zoom)
        panel_w = float(panel_size.width())
        panel_h = float(panel_size.height())
        return not self._axis_fits(img_w, panel_w) or not self._axis_fits(
            img_h, panel_h
        )

    def setZoom1To1(self, anchor: QPoint | QPointF | None = None):
        """Snap zoom to native pixel ratio, optionally anchoring to a panel point.

        Args:
            anchor: Optional panel coordinate to keep stationary while zooming.
        """
        if self.content_size.isNull():
            return
        if self._pan_zoom_locked:
            return
        self._stop_zoom_animation()
        self.zoom_mode = ViewportZoomMode.ONE_TO_ONE
        old_zoom = self.zoom
        new_zoom = self.nativeZoom()
        if anchor is not None:
            context = CoordinateContext(self.qpane)
            physical_anchor = context.logical_to_physical(QPointF(anchor))
            panel_center_physical = context.logical_to_physical(
                QPointF(self.qpane.width() / 2, self.qpane.height() / 2)
            )
            rel = physical_anchor - panel_center_physical - self.pan
            image_point = rel / old_zoom if old_zoom != 0 else QPointF(0, 0)
            new_pan = physical_anchor - panel_center_physical - image_point * new_zoom
        else:
            new_pan = QPointF(0, 0)
        self._commit_zoom_change(new_zoom, new_pan)

    def setZoom1To1Interpolated(self, anchor: QPoint | QPointF | None = None) -> None:
        """Snap zoom to native pixel ratio using an interpolated transition."""
        if self.content_size.isNull():
            return
        if self._pan_zoom_locked:
            return
        target_zoom = self.nativeZoom()
        target_pan = None if anchor is not None else QPointF(0, 0)
        self._apply_zoom_interpolated(
            requested_zoom=target_zoom,
            anchor=anchor,
            target_mode=ViewportZoomMode.ONE_TO_ONE,
            request_delta_ms=None,
            target_pan=target_pan,
            fit_zoom=None,
        )

    def setZoomFit(self):
        """Fit the content within the viewport and recenter pan."""
        if self.content_size.isNull():
            return
        if self._pan_zoom_locked:
            return
        self._stop_zoom_animation()
        self.zoom_mode = ViewportZoomMode.FIT
        viewport_phys = self.qpane.physicalViewportRect()
        panel_w = viewport_phys.width()
        panel_h = viewport_phys.height()
        img_w, img_h = self.content_size.width(), self.content_size.height()
        if img_w == 0 or img_h == 0:
            return
        # This logic differs from QPane.applyZoom to account for transition effects
        fit_zoom = min(panel_w / img_w, panel_h / img_h)
        zoom = fit_zoom
        self.fit_zoom = zoom
        self._commit_zoom_change(zoom, QPointF(0, 0))

    def setZoomFitInterpolated(self) -> None:
        """Fit the content within the viewport using an interpolated transition."""
        if self.content_size.isNull():
            return
        if self._pan_zoom_locked:
            return
        viewport_phys = self.qpane.physicalViewportRect()
        panel_w = viewport_phys.width()
        panel_h = viewport_phys.height()
        img_w, img_h = self.content_size.width(), self.content_size.height()
        if img_w == 0 or img_h == 0:
            return
        fit_zoom = min(panel_w / img_w, panel_h / img_h)
        self._apply_zoom_interpolated(
            requested_zoom=fit_zoom,
            anchor=None,
            target_mode=ViewportZoomMode.FIT,
            request_delta_ms=None,
            target_pan=QPointF(0, 0),
            fit_zoom=fit_zoom,
        )

    def applyZoom(self, requested_zoom: float, anchor: QPoint | QPointF | None = None):
        """Apply the requested zoom, keeping an anchor steady when provided and clamping pan to bounds."""
        if self.content_size.isNull() or self._pan_zoom_locked:
            return
        self._stop_zoom_animation()
        self.zoom_mode = ViewportZoomMode.CUSTOM
        self._apply_zoom_immediate(requested_zoom, anchor)

    def applyZoomInterpolated(
        self, requested_zoom: float, anchor: QPoint | QPointF | None = None
    ) -> None:
        """Apply the requested zoom using a short interpolation window."""
        if self.content_size.isNull() or self._pan_zoom_locked:
            return
        request_delta_ms = self._record_zoom_request_time()
        self._apply_zoom_interpolated(
            requested_zoom=requested_zoom,
            anchor=anchor,
            target_mode=ViewportZoomMode.CUSTOM,
            request_delta_ms=request_delta_ms,
            target_pan=None,
            fit_zoom=None,
        )

    def applyZoomInterpolatedWithMode(
        self,
        requested_zoom: float,
        anchor: QPoint | QPointF | None = None,
        *,
        target_mode: ViewportZoomMode,
        target_pan: QPointF | None = None,
        fit_zoom: float | None = None,
    ) -> None:
        """Apply an interpolated zoom request while setting the requested mode."""
        if self.content_size.isNull() or self._pan_zoom_locked:
            return
        request_delta_ms = self._record_zoom_request_time()
        self._apply_zoom_interpolated(
            requested_zoom=requested_zoom,
            anchor=anchor,
            target_mode=target_mode,
            request_delta_ms=request_delta_ms,
            target_pan=target_pan,
            fit_zoom=fit_zoom,
        )

    def _apply_zoom_interpolated(
        self,
        *,
        requested_zoom: float,
        anchor: QPoint | QPointF | None,
        target_mode: ViewportZoomMode,
        request_delta_ms: float | None,
        target_pan: QPointF | None,
        fit_zoom: float | None,
    ) -> None:
        """Apply a zoom request with interpolation and update the zoom mode."""
        old_zoom = self.zoom
        new_zoom = requested_zoom
        self.zoom_mode = target_mode
        if target_mode == ViewportZoomMode.FIT and fit_zoom is not None:
            self.fit_zoom = fit_zoom
        self._smooth_zoom_last_burst = bool(
            request_delta_ms is not None
            and request_delta_ms <= self._smooth_zoom_burst_threshold_ms
        )
        if request_delta_ms is not None and self._should_apply_zoom_immediately(
            request_delta_ms
        ):
            self._stop_zoom_animation()
            if target_pan is None:
                self._apply_zoom_immediate(new_zoom, anchor)
            else:
                self._commit_zoom_change(
                    new_zoom, self._clamp_pan_for_zoom(new_zoom, target_pan)
                )
            return
        if self._should_interpolate_zoom(old_zoom, new_zoom):
            duration_ms = self._pick_zoom_duration_ms(request_delta_ms)
            min_frame_ms = self._get_zoom_target_frame_ms()
            if self._should_start_zoom_animation(duration_ms, min_frame_ms):
                self._start_zoom_animation(
                    new_zoom,
                    anchor,
                    duration_ms=duration_ms,
                    min_frame_ms=min_frame_ms,
                    target_pan=target_pan,
                )
                return
        self._stop_zoom_animation()
        if target_pan is None:
            self._apply_zoom_immediate(new_zoom, anchor)
            return
        self._commit_zoom_change(
            new_zoom, self._clamp_pan_for_zoom(new_zoom, target_pan)
        )

    def get_transform(
        self,
        source_image_size: QSize,
        pyramid_scale: float = 1.0,
        pan_override: QPointF = None,
        content_snapshot: SceneContentSnapshot | None = None,
    ) -> QTransform:
        """Build the transform mapping source image pixels to logical panel pixels for the current zoom and pan."""
        context = CoordinateContext(
            self.qpane,
            pan_override=pan_override,
            content_snapshot=content_snapshot or self._current_content_snapshot(),
        )
        return context.get_painter_transform(source_image_size, pyramid_scale)

    def panel_to_content_point(self, panel_pos: QPoint) -> QPoint | None:
        """Project a panel coordinate into image space when content is available."""
        content_snapshot = self._current_content_snapshot()
        if content_snapshot is None:
            return None
        context = CoordinateContext(self.qpane, content_snapshot=content_snapshot)
        return context.panel_to_image(QPointF(panel_pos))

    def panel_hit_test(self, panel_pos: QPoint) -> PanelHitTest | None:
        """Return content hit-test metadata for a panel coordinate when content is available."""
        content_snapshot = self._current_content_snapshot()
        if content_snapshot is None:
            return None
        context = CoordinateContext(self.qpane, content_snapshot=content_snapshot)
        return context.panel_to_image_hit(QPointF(panel_pos))

    def content_to_panel_point(self, content_point: QPoint) -> QPointF | None:
        """Project an image-space coordinate into panel space when content is available."""
        content_snapshot = self._current_content_snapshot()
        if content_snapshot is None:
            return None
        context = CoordinateContext(self.qpane, content_snapshot=content_snapshot)
        return context.image_to_panel(QPointF(content_point))

    def smooth_zoom_diagnostics(self) -> SmoothZoomDiagnostics | None:
        """Return smooth zoom timing data for diagnostics overlays."""
        if not self._smooth_zoom_enabled:
            return None
        fps = self._smooth_zoom_fallback_fps
        using_fallback = True
        if self._smooth_zoom_use_display_fps:
            refresh = self._get_display_refresh_rate()
            if refresh > 0:
                fps = refresh
                using_fallback = False
        frame_ms = 1000.0 / fps if fps > 0 else 0.0
        mode = "burst" if self._smooth_zoom_last_burst else "normal"
        return SmoothZoomDiagnostics(
            fps_hz=fps,
            using_fallback_fps=using_fallback,
            frame_ms=frame_ms,
            mode=mode,
        )

    def update_detected_refresh_rate(self, refresh_rate: float | None) -> None:
        """Persist the latest detected display refresh rate for diagnostics."""
        if refresh_rate is None:
            self._detected_refresh_rate = None
        else:
            try:
                numeric = float(refresh_rate)
            except (TypeError, ValueError):
                self._detected_refresh_rate = None
            else:
                self._detected_refresh_rate = numeric if numeric > 0 else None
        self._mark_diagnostics_dirty()

    def _physical_viewport_size(self) -> QSizeF:
        """Return the viewport size in device pixels using QPane.physicalViewportRect."""
        rect = self.qpane.physicalViewportRect()
        if rect is None:
            raise ValueError("QPane.physicalViewportRect must return a QRectF")
        return rect.size()

    def _current_content_snapshot(self) -> SceneContentSnapshot | None:
        """Return renderable content geometry from the owning view."""
        view_accessor = getattr(self.qpane, "view", None)
        view = view_accessor() if callable(view_accessor) else None
        snapshot_getter = getattr(view, "current_content_snapshot", None)
        if not callable(snapshot_getter):
            return None
        return snapshot_getter()

    def _commit_zoom_change(self, zoom: float, pan: QPointF):
        """Clamp pan and zoom, update stored state, and emit viewChanged when values change."""
        panel_size = self._physical_viewport_size()
        clamped_pan = self.clampPan(pan, zoom, panel_size, self.content_size)
        if self.zoom == zoom and self.pan == clamped_pan:
            return
        self.zoom = zoom
        self.pan = clamped_pan
        self.viewChanged.emit()
        self._mark_diagnostics_dirty()

    def _should_interpolate_zoom(self, old_zoom: float, new_zoom: float) -> bool:
        """Return True when zoom interpolation should be used for this change."""
        if not self._smooth_zoom_enabled:
            return False
        if abs(new_zoom - old_zoom) <= self._SMOOTH_ZOOM_MIN_DELTA:
            return False
        if (
            self._smooth_zoom_duration_ms <= 0
            and self._smooth_zoom_burst_duration_ms <= 0
        ):
            return False
        return True

    @staticmethod
    def _should_start_zoom_animation(duration_ms: int, min_frame_ms: int) -> bool:
        """Return True when the duration allows at least one animation frame."""
        return duration_ms >= min_frame_ms

    def _start_zoom_animation(
        self,
        target_zoom: float,
        anchor: QPoint | QPointF | None,
        *,
        duration_ms: int,
        min_frame_ms: int,
        target_pan: QPointF | None,
    ) -> None:
        """Begin an interpolated zoom from the current state to ``target_zoom``."""
        if self._pan_zoom_locked:
            return
        self._zoom_anim_start_zoom = self.zoom
        self._zoom_anim_target_zoom = target_zoom
        self._zoom_anim_duration_ms = max(0, int(duration_ms))
        self._zoom_anim_min_frame_ms = max(1, int(min_frame_ms))
        self._zoom_anim_start_pan = self.pan
        self._zoom_anim_target_pan = target_pan
        if anchor is None:
            self._zoom_anim_anchor_physical = None
        else:
            context = CoordinateContext(self.qpane)
            self._zoom_anim_anchor_physical = context.logical_to_physical(
                QPointF(anchor)
            )
        self._zoom_anim_panel_center = CoordinateContext(
            self.qpane
        ).logical_to_physical(QPointF(self.qpane.width() / 2, self.qpane.height() / 2))
        self._zoom_anim_elapsed.restart()
        self._zoom_anim_pending = True
        self._zoom_anim_last_tick_ms = None
        self._zoom_anim_timer.setInterval(self._zoom_anim_min_frame_ms)
        if not self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.start()

    def _stop_zoom_animation(self) -> None:
        """Stop any active zoom interpolation."""
        self._zoom_anim_pending = False
        self._zoom_anim_last_tick_ms = None
        self._zoom_anim_target_pan = None
        if self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.stop()

    def _tick_zoom_animation(self) -> None:
        """Advance the zoom interpolation and emit view updates."""
        if not self._zoom_anim_pending:
            self._stop_zoom_animation()
            return
        now_ms = time.monotonic() * 1000.0
        if self._zoom_anim_last_tick_ms is not None:
            delta_ms = now_ms - self._zoom_anim_last_tick_ms
            if delta_ms < self._zoom_anim_min_frame_ms:
                return
        elapsed_ms = self._zoom_anim_elapsed.elapsed()
        duration = self._zoom_anim_duration_ms
        if duration <= 0:
            t = 1.0
        else:
            t = min(1.0, max(0.0, elapsed_ms / duration))
        if t >= 1.0:
            zoom = self._zoom_anim_target_zoom
            pan = self._exact_zoom_animation_target_pan()
        else:
            eased = 1.0 - (1.0 - t) * (1.0 - t)
            zoom = self._zoom_anim_start_zoom + (
                (self._zoom_anim_target_zoom - self._zoom_anim_start_zoom) * eased
            )
            if self._zoom_anim_target_pan is None:
                pan = self._compute_anchor_pan(
                    self._zoom_anim_start_zoom,
                    self._zoom_anim_start_pan,
                    zoom,
                    None,
                    anchor_physical=self._zoom_anim_anchor_physical,
                    panel_center=self._zoom_anim_panel_center,
                )
            else:
                pan = self._zoom_anim_start_pan + (
                    (self._zoom_anim_target_pan - self._zoom_anim_start_pan) * eased
                )
        self._commit_zoom_change(zoom, self._clamp_pan_for_zoom(zoom, pan))
        self._zoom_anim_last_tick_ms = now_ms
        if t >= 1.0:
            self._zoom_anim_pending = False
            self._stop_zoom_animation()

    def _exact_zoom_animation_target_pan(self) -> QPointF:
        """Return the exact pan target for a completed zoom animation."""
        if self._zoom_anim_target_pan is not None:
            return QPointF(self._zoom_anim_target_pan)
        return self._compute_anchor_pan(
            self._zoom_anim_start_zoom,
            self._zoom_anim_start_pan,
            self._zoom_anim_target_zoom,
            None,
            anchor_physical=self._zoom_anim_anchor_physical,
            panel_center=self._zoom_anim_panel_center,
        )

    def _compute_anchor_pan(
        self,
        start_zoom: float,
        start_pan: QPointF,
        new_zoom: float,
        anchor: QPoint | QPointF | None,
        *,
        anchor_physical: QPointF | None = None,
        panel_center: QPointF | None = None,
    ) -> QPointF:
        """Return a pan delta that keeps ``anchor`` stationary while zooming."""
        if anchor is None and anchor_physical is None:
            return start_pan
        if panel_center is None:
            panel_center = CoordinateContext(self.qpane).logical_to_physical(
                QPointF(self.qpane.width() / 2, self.qpane.height() / 2)
            )
        if anchor_physical is not None:
            physical_anchor = anchor_physical
        else:
            physical_anchor = CoordinateContext(self.qpane).logical_to_physical(
                QPointF(anchor)
            )
        rel = physical_anchor - panel_center - start_pan
        scale = new_zoom / start_zoom if start_zoom != 0 else 1.0
        return start_pan - rel * (scale - 1)

    def _clamp_pan_for_zoom(self, zoom: float, pan: QPointF) -> QPointF:
        """Recenter pan when the zoomed content fits inside the viewport."""
        panel_size = self._physical_viewport_size()
        img_w = self.content_size.width() * zoom
        img_h = self.content_size.height() * zoom
        if self._axis_fits(img_w, panel_size.width()) and self._axis_fits(
            img_h, panel_size.height()
        ):
            return QPointF(0, 0)
        return pan

    def _apply_zoom_immediate(
        self, requested_zoom: float, anchor: QPoint | QPointF | None
    ) -> None:
        """Apply a zoom request immediately without interpolation."""
        new_pan = self._compute_anchor_pan(self.zoom, self.pan, requested_zoom, anchor)
        self._commit_zoom_change(
            requested_zoom, self._clamp_pan_for_zoom(requested_zoom, new_pan)
        )

    def _record_zoom_request_time(self) -> float | None:
        """Return the delta since the last zoom request and update the timestamp."""
        now = time.monotonic()
        if self._zoom_last_request_time is None:
            self._zoom_last_request_time = now
            return None
        delta = now - self._zoom_last_request_time
        self._zoom_last_request_time = now
        return delta * 1000.0

    def _should_apply_zoom_immediately(self, delta_ms: float | None) -> bool:
        """Return True when zoom interpolation should be skipped for bursts."""
        if delta_ms is None:
            return False
        return delta_ms <= self._smooth_zoom_burst_threshold_ms

    def _pick_zoom_duration_ms(self, delta_ms: float | None) -> int:
        """Return an interpolation duration based on the request cadence."""
        if delta_ms is None:
            return self._smooth_zoom_duration_ms
        if delta_ms <= self._smooth_zoom_fast_threshold_ms:
            return self._smooth_zoom_burst_duration_ms
        if delta_ms >= self._smooth_zoom_slow_threshold_ms:
            return self._smooth_zoom_duration_ms
        ratio = (delta_ms - self._smooth_zoom_fast_threshold_ms) / (
            self._smooth_zoom_slow_threshold_ms - self._smooth_zoom_fast_threshold_ms
        )
        span = self._smooth_zoom_duration_ms - self._smooth_zoom_burst_duration_ms
        return int(round(self._smooth_zoom_burst_duration_ms + span * ratio))

    def _get_zoom_target_frame_ms(self) -> int:
        """Return the ideal frame interval derived from the active screen refresh."""
        fps = self._get_active_zoom_fps()
        return max(1, int(round(1000.0 / fps)))

    def _get_active_zoom_fps(self) -> float:
        """Return the FPS target used for smooth-zoom interpolation."""
        if self._smooth_zoom_use_display_fps:
            refresh = self._get_display_refresh_rate()
            if refresh > 0:
                return refresh
        return self._smooth_zoom_fallback_fps

    def _get_display_refresh_rate(self) -> float:
        """Return the best-guess screen refresh rate for the qpane."""
        if self._detected_refresh_rate is not None:
            return self._detected_refresh_rate
        window = None
        if hasattr(self.qpane, "windowHandle"):
            window = self.qpane.windowHandle()
        if window is None and hasattr(self.qpane, "window"):
            parent_window = self.qpane.window()
            if parent_window is not None:
                window = parent_window.windowHandle()
        if window is None:
            return 0.0
        screen = window.screen()
        if screen is None:
            return 0.0
        refresh = float(screen.refreshRate())
        if refresh > 0:
            self._detected_refresh_rate = refresh
            return refresh
        return 0.0

    def _refresh_smooth_zoom_settings(self) -> None:
        """Sync smooth-zoom settings from the current configuration snapshot."""
        config = self._config
        self._smooth_zoom_enabled = bool(getattr(config, "smooth_zoom_enabled", True))
        self._smooth_zoom_duration_ms = self._coerce_non_negative_int(
            getattr(config, "smooth_zoom_duration_ms", 80),
            fallback=80,
        )
        self._smooth_zoom_burst_duration_ms = self._coerce_non_negative_int(
            getattr(config, "smooth_zoom_burst_duration_ms", 20),
            fallback=20,
        )
        if self._smooth_zoom_burst_duration_ms > self._smooth_zoom_duration_ms:
            self._smooth_zoom_burst_duration_ms = self._smooth_zoom_duration_ms
        self._smooth_zoom_burst_threshold_ms = self._coerce_non_negative_float(
            getattr(config, "smooth_zoom_burst_threshold_ms", 25),
            fallback=25.0,
        )
        self._smooth_zoom_fast_threshold_ms = self._SMOOTH_ZOOM_FAST_THRESHOLD_MS
        self._smooth_zoom_slow_threshold_ms = self._SMOOTH_ZOOM_SLOW_THRESHOLD_MS
        self._smooth_zoom_fallback_fps = self._coerce_positive_float(
            getattr(config, "smooth_zoom_fallback_fps", 60.0),
            fallback=60.0,
        )
        self._smooth_zoom_use_display_fps = bool(
            getattr(config, "smooth_zoom_use_display_fps", True)
        )

    @staticmethod
    def _coerce_non_negative_int(value: object, *, fallback: int) -> int:
        """Return a non-negative integer or a safe fallback."""
        try:
            numeric = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return int(fallback)
        return max(0, numeric)

    @staticmethod
    def _coerce_non_negative_float(value: object, *, fallback: float) -> float:
        """Return a non-negative float or a safe fallback."""
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return float(fallback)
        return max(0.0, numeric)

    @staticmethod
    def _coerce_positive_float(value: object, *, fallback: float) -> float:
        """Return a positive float or a safe fallback."""
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return float(fallback)
        if numeric <= 0:
            return float(fallback)
        return numeric

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

    @classmethod
    def _axis_fits(cls, scaled_axis: float, panel_axis: float) -> bool:
        """Return True when scaled content fits an axis within pan tolerance."""
        return scaled_axis <= panel_axis + cls._PAN_EPSILON_PX
