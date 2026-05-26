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

"""Unit tests for Viewport smooth zoom interpolation logic."""

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtWidgets import QWidget

from qpane.core import Config
from qpane.rendering.viewport import Viewport, ViewportZoomMode

from PySide6.QtGui import QImage


class MockView:
    def __init__(self, viewport):
        self.viewport = viewport


class MockQPane(QWidget):
    def __init__(self):
        super().__init__()
        self._rect = QRectF(0, 0, 800, 600)
        self.original_image = QImage(1000, 1000, QImage.Format.Format_RGB32)
        self._view = None

    def physicalViewportRect(self):
        return self._rect

    def width(self):
        return int(self._rect.width())

    def height(self):
        return int(self._rect.height())

    def view(self):
        return self._view


@pytest.fixture
def viewport_setup(qapp):
    qpane = MockQPane()
    config = Config()
    viewport = Viewport(qpane, config)
    viewport.setContentSize(QSize(1000, 1000))

    # Wire up the view mock
    qpane._view = MockView(viewport)

    return viewport, qpane


def test_smooth_zoom_interpolation(viewport_setup):
    """Verify that zoom interpolates correctly over time using the easing function."""
    viewport, qpane = viewport_setup

    # Initial state
    viewport.zoom = 1.0
    viewport.pan = QPointF(0, 0)

    # Mock time and elapsed timer to control the animation loop deterministically
    with patch("qpane.rendering.viewport.time") as mock_time:
        # Setup mocks
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed

        # Start time
        start_time = 1000.0
        mock_time.monotonic.return_value = start_time

        # Start animation: Zoom from 1.0 to 2.0 over 100ms
        target_zoom = 2.0
        duration_ms = 100
        min_frame_ms = 16

        viewport._start_zoom_animation(
            target_zoom=target_zoom,
            anchor=None,
            duration_ms=duration_ms,
            min_frame_ms=min_frame_ms,
            target_pan=QPointF(0, 0),
        )

        assert viewport._zoom_anim_pending is True
        assert viewport.zoom == 1.0

        # Step 1: 50ms elapsed (50% progress)
        # Easing is: 1.0 - (1.0 - t) * (1.0 - t)
        # t = 0.5 -> eased = 1.0 - 0.25 = 0.75
        # Expected zoom = 1.0 + (2.0 - 1.0) * 0.75 = 1.75

        mock_time.monotonic.return_value = start_time + 0.050  # +50ms
        mock_elapsed.elapsed.return_value = 50

        viewport._tick_zoom_animation()

        assert viewport.zoom == pytest.approx(1.75, abs=0.001)
        assert viewport._zoom_anim_pending is True

        # Step 2: 100ms elapsed (100% progress)
        mock_time.monotonic.return_value = start_time + 0.100  # +100ms
        mock_elapsed.elapsed.return_value = 100

        viewport._tick_zoom_animation()

        assert viewport.zoom == pytest.approx(2.0, abs=0.001)
        assert viewport._zoom_anim_pending is False


def test_zoom_animation_timer_uses_precise_timer(viewport_setup):
    """Smooth zoom should use a precise timer for steadier animation cadence."""
    viewport, _ = viewport_setup
    assert viewport._zoom_anim_timer.timerType() == Qt.TimerType.PreciseTimer


def test_smooth_zoom_clamping(viewport_setup):
    """Verify that animation finishes cleanly even if time jumps past duration."""
    viewport, qpane = viewport_setup
    viewport.zoom = 1.0

    with patch("qpane.rendering.viewport.time") as mock_time:
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed

        start_time = 1000.0
        mock_time.monotonic.return_value = start_time

        viewport._start_zoom_animation(
            target_zoom=2.0,
            anchor=None,
            duration_ms=100,
            min_frame_ms=16,
            target_pan=QPointF(0, 0),
        )

        # Jump way past end
        mock_time.monotonic.return_value = start_time + 5.0  # +5000ms
        mock_elapsed.elapsed.return_value = 5000

        viewport._tick_zoom_animation()

        assert viewport.zoom == 2.0
        assert viewport._zoom_anim_pending is False


def test_smooth_zoom_completion_commits_exact_target_zoom(viewport_setup):
    """Completed animations should land on the requested target zoom exactly."""
    viewport, _ = viewport_setup
    target_zoom = 0.2887369791666667
    viewport.zoom = 1.0
    viewport.pan = QPointF(12, -8)

    with patch("qpane.rendering.viewport.time") as mock_time:
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed
        mock_time.monotonic.return_value = 1000.0

        viewport._start_zoom_animation(
            target_zoom=target_zoom,
            anchor=None,
            duration_ms=80,
            min_frame_ms=16,
            target_pan=QPointF(0, 0),
        )

        mock_time.monotonic.return_value = 1000.081
        mock_elapsed.elapsed.return_value = 81

        viewport._tick_zoom_animation()

    assert viewport.zoom == target_zoom
    assert viewport._zoom_anim_pending is False


def test_fit_zoom_completion_commits_exact_target_pan(viewport_setup):
    """Completed fit animations should recenter pan exactly."""
    viewport, _ = viewport_setup
    target_zoom = 0.6
    viewport.zoom = 1.0
    viewport.pan = QPointF(48, -32)

    with patch("qpane.rendering.viewport.time") as mock_time:
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed
        mock_time.monotonic.return_value = 1000.0

        viewport._apply_zoom_interpolated(
            requested_zoom=target_zoom,
            anchor=None,
            target_mode=ViewportZoomMode.FIT,
            request_delta_ms=None,
            target_pan=QPointF(0, 0),
            fit_zoom=target_zoom,
        )

        mock_time.monotonic.return_value = 1000.081
        mock_elapsed.elapsed.return_value = 81

        viewport._tick_zoom_animation()

    assert viewport.zoom == target_zoom
    assert viewport.pan == QPointF(0, 0)
    assert viewport.get_zoom_mode() == viewport.zoom_mode.FIT


def test_anchored_one_to_one_completion_uses_exact_native_zoom(viewport_setup):
    """Completed anchored 1:1 animations should land on native zoom exactly."""
    viewport, _ = viewport_setup
    viewport.zoom = 0.5
    viewport.pan = QPointF(0, 0)

    with patch("qpane.rendering.viewport.time") as mock_time:
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed
        mock_time.monotonic.return_value = 1000.0

        viewport.setZoom1To1Interpolated(anchor=QPointF(120, 160))

        mock_time.monotonic.return_value = 1000.081
        mock_elapsed.elapsed.return_value = 81

        viewport._tick_zoom_animation()

    assert viewport.zoom == viewport.nativeZoom()
    assert viewport._zoom_anim_pending is False


def test_smooth_zoom_frame_skipping(viewport_setup):
    """Verify that updates are skipped if the delta time is less than min_frame_ms."""
    viewport, qpane = viewport_setup
    viewport.zoom = 1.0

    with patch("qpane.rendering.viewport.time") as mock_time:
        mock_elapsed = MagicMock()
        viewport._zoom_anim_elapsed = mock_elapsed

        start_time = 1000.0
        mock_time.monotonic.return_value = start_time
        mock_elapsed.elapsed.return_value = 0

        viewport._start_zoom_animation(
            target_zoom=2.0,
            anchor=None,
            duration_ms=100,
            min_frame_ms=16,  # 16ms per frame
            target_pan=QPointF(0, 0),
        )

        # First tick sets _zoom_anim_last_tick_ms (if it wasn't set)
        # Actually _start_zoom_animation sets _zoom_anim_last_tick_ms to None
        # The first call to _tick_zoom_animation will always run because last_tick is None
        viewport._tick_zoom_animation()

        # Now last_tick is set to start_time

        # Advance only 5ms
        mock_time.monotonic.return_value = start_time + 0.005
        mock_elapsed.elapsed.return_value = 5

        # Should NOT update zoom yet because 5ms < 16ms
        current_zoom = viewport.zoom
        viewport._tick_zoom_animation()
        assert viewport.zoom == current_zoom

        # Advance to 20ms (enough for frame)
        mock_time.monotonic.return_value = start_time + 0.020
        mock_elapsed.elapsed.return_value = 20

        viewport._tick_zoom_animation()
        assert viewport.zoom > current_zoom


def test_should_interpolate_zoom(viewport_setup):
    """Verify conditions under which interpolation is enabled."""
    viewport, _ = viewport_setup

    # Enable smooth zoom
    viewport._smooth_zoom_enabled = True
    viewport._smooth_zoom_duration_ms = 100

    # Small delta -> No interpolation
    assert not viewport._should_interpolate_zoom(1.0, 1.00001)

    # Large delta -> Interpolation
    assert viewport._should_interpolate_zoom(1.0, 1.5)

    # Disabled -> No interpolation
    viewport._smooth_zoom_enabled = False
    assert not viewport._should_interpolate_zoom(1.0, 1.5)


def test_pick_zoom_duration_ms_bounds(viewport_setup):
    """Zoom duration should honor fast/slow thresholds."""
    viewport, _ = viewport_setup
    viewport._smooth_zoom_duration_ms = 120
    viewport._smooth_zoom_burst_duration_ms = 40
    viewport._smooth_zoom_fast_threshold_ms = 10.0
    viewport._smooth_zoom_slow_threshold_ms = 100.0
    assert viewport._pick_zoom_duration_ms(5.0) == 40
    assert viewport._pick_zoom_duration_ms(120.0) == 120
    mid = viewport._pick_zoom_duration_ms(55.0)
    assert 40 <= mid <= 120


def test_clamp_pan_recenters_when_content_fits(viewport_setup):
    """Pan should reset to zero when the zoomed content fits the panel."""
    viewport, _ = viewport_setup
    viewport.setContentSize(QSize(10, 10))
    clamped = viewport._clamp_pan_for_zoom(0.1, QPointF(5, 5))
    assert clamped == QPointF(0, 0)


def test_apply_zoom_interpolated_burst_skips_animation(viewport_setup):
    """Burst zoom requests should apply immediately without animation."""
    viewport, _ = viewport_setup
    viewport._smooth_zoom_burst_threshold_ms = 30.0
    viewport.zoom = 1.0
    viewport.pan = QPointF(0, 0)
    viewport._apply_zoom_interpolated(
        requested_zoom=2.0,
        anchor=None,
        target_mode=viewport.zoom_mode,
        request_delta_ms=10.0,
        target_pan=None,
        fit_zoom=None,
    )
    assert viewport.zoom == 2.0
    assert viewport._zoom_anim_pending is False


def test_active_zoom_animation_burst_request_applies_immediately(viewport_setup):
    """Burst requests during an active animation should interrupt and apply."""
    viewport, _ = viewport_setup
    viewport._smooth_zoom_burst_threshold_ms = 30.0
    viewport._smooth_zoom_burst_duration_ms = 10
    viewport.zoom = 1.0
    viewport.pan = QPointF(0, 0)
    viewport._zoom_anim_min_frame_ms = 16
    viewport._start_zoom_animation(
        target_zoom=2.0,
        anchor=None,
        duration_ms=80,
        min_frame_ms=16,
        target_pan=None,
    )
    viewport._apply_zoom_interpolated(
        requested_zoom=3.0,
        anchor=None,
        target_mode=ViewportZoomMode.CUSTOM,
        request_delta_ms=10.0,
        target_pan=None,
        fit_zoom=None,
    )
    assert viewport.zoom == 3.0
    assert viewport._zoom_anim_pending is False


def test_update_detected_refresh_rate_rejects_invalid(viewport_setup):
    """Refresh rate updates should ignore invalid values."""
    viewport, _ = viewport_setup
    viewport.update_detected_refresh_rate(-1)
    assert viewport._detected_refresh_rate is None
    viewport.update_detected_refresh_rate("bad")  # type: ignore[arg-type]
    assert viewport._detected_refresh_rate is None


def test_smooth_zoom_diagnostics_uses_display_refresh_when_available(viewport_setup):
    """Diagnostics should report display refresh rate and fallback correctly."""
    viewport, _ = viewport_setup
    viewport._smooth_zoom_enabled = True
    viewport._smooth_zoom_use_display_fps = True
    viewport._smooth_zoom_fallback_fps = 60.0
    viewport.update_detected_refresh_rate(120.0)
    diagnostics = viewport.smooth_zoom_diagnostics()
    assert diagnostics is not None
    assert diagnostics.using_fallback_fps is False
    assert diagnostics.fps_hz == 120.0
    assert diagnostics.frame_ms == pytest.approx(1000.0 / 120.0)
    viewport.update_detected_refresh_rate(None)
    viewport._smooth_zoom_use_display_fps = False
    diagnostics = viewport.smooth_zoom_diagnostics()
    assert diagnostics is not None
    assert diagnostics.using_fallback_fps is True
    assert diagnostics.fps_hz == 60.0
