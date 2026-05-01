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

"""Coordinate conversions and unit helpers for the rendering stack."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, QSizeF
from PySide6.QtGui import QTransform

if TYPE_CHECKING:
    from ..qpane import QPane
    from ..scene.render_plan import SceneContentSnapshot
    from .viewport import ViewportZoomMode
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedViewState:
    """Viewport center, visible width fraction, and zoom mode snapshot."""

    center_x: float
    center_y: float
    zoom_frac: float
    zoom_mode: "ViewportZoomMode"


@dataclass(frozen=True)
class PanelHitTest:
    """Result of mapping a panel coordinate into image space."""

    panel_point: QPoint
    raw_point: QPointF
    clamped_point: QPoint
    inside_image: bool


@dataclass(frozen=True, slots=True)
class LogicalPoint:
    """Device-independent point defined in Qt logical pixels.

    Use this for widget geometry, input events, and qpane math before applying
    the device pixel ratio.
    Call to_physical(dpr=float) to obtain the matching PhysicalPoint.
    """

    x: float
    y: float

    @classmethod
    def from_qt(cls, point: QPointF | QPoint) -> "LogicalPoint":
        """Build a LogicalPoint copy from a Qt ``point`` variant."""
        qpoint = QPointF(point)
        return cls(qpoint.x(), qpoint.y())

    def to_qt(self) -> QPointF:
        """Return a QPointF representing this logical coordinate."""
        return QPointF(self.x, self.y)

    def plus(self, other: "LogicalPoint") -> "LogicalPoint":
        """Return a new LogicalPoint translated by ``other``."""
        return LogicalPoint(self.x + other.x, self.y + other.y)

    def minus(self, other: "LogicalPoint") -> "LogicalPoint":
        """Return a new LogicalPoint offset by ``other`` in the opposite direction."""
        return LogicalPoint(self.x - other.x, self.y - other.y)

    def scaled(self, factor: float) -> "LogicalPoint":
        """Return a LogicalPoint scaled by ``factor`` along each axis."""
        return LogicalPoint(self.x * factor, self.y * factor)

    def to_physical(self, dpr: float) -> "PhysicalPoint":
        """Convert logical pixels into physical coordinates using ``dpr``."""
        return PhysicalPoint(self.x * dpr, self.y * dpr)


@dataclass(frozen=True, slots=True)
class PhysicalPoint:
    """Point expressed in physical device pixels.

    Use this when interacting with renderer buffers or any data already scaled
    by the device pixel ratio.
    Call to_logical(dpr=float) to recover a LogicalPoint.
    """

    x: float
    y: float

    @classmethod
    def from_qt(cls, point: QPointF | QPoint) -> "PhysicalPoint":
        """Build a PhysicalPoint from a QPoint/QPointF."""
        qpoint = QPointF(point)
        return cls(qpoint.x(), qpoint.y())

    def to_qt(self) -> QPointF:
        """Represent this physical coordinate as a QPointF."""
        return QPointF(self.x, self.y)

    def plus(self, other: "PhysicalPoint") -> "PhysicalPoint":
        """Return a PhysicalPoint shifted by ``other``."""
        return PhysicalPoint(self.x + other.x, self.y + other.y)

    def minus(self, other: "PhysicalPoint") -> "PhysicalPoint":
        """Return a PhysicalPoint offset opposite ``other``."""
        return PhysicalPoint(self.x - other.x, self.y - other.y)

    def scaled(self, factor: float) -> "PhysicalPoint":
        """Scale this point's coordinates by ``factor``."""
        return PhysicalPoint(self.x * factor, self.y * factor)

    def to_logical(self, dpr: float) -> LogicalPoint:
        """Convert physical device pixels into logical pixels via ``dpr``."""
        divisor = dpr if dpr > 0 else 1.0
        return LogicalPoint(self.x / divisor, self.y / divisor)


@dataclass(frozen=True, slots=True)
class LogicalSize:
    """Size defined in Qt logical pixels.

    Use when reasoning about widget layouts or viewport sizes prior to DPR
    application.
    Call to_physical(dpr=float) when a PhysicalSize is required.
    """

    width: float
    height: float

    @classmethod
    def from_qt(cls, size: QSizeF | QSize) -> "LogicalSize":
        """Return a LogicalSize copy of the provided Qt ``size``."""
        qsize = QSizeF(size)
        return cls(qsize.width(), qsize.height())

    def to_qt(self) -> QSizeF:
        """Represent this logical size as a QSizeF."""
        return QSizeF(self.width, self.height)

    def to_physical(self, dpr: float) -> "PhysicalSize":
        """Scale this logical size by ``dpr`` to obtain physical dimensions."""
        return PhysicalSize(self.width * dpr, self.height * dpr)

    def half_point(self) -> LogicalPoint:
        """Return the logical midpoint of this size."""
        return LogicalPoint(self.width / 2.0, self.height / 2.0)


@dataclass(frozen=True, slots=True)
class PhysicalSize:
    """Size defined in physical device pixels.

    Use when allocating buffers or comparing against image dimensions that
    include DPR scaling.
    Call to_logical(dpr=float) to obtain the matching LogicalSize.
    """

    width: float
    height: float

    @classmethod
    def from_qt(cls, size: QSizeF | QSize) -> "PhysicalSize":
        """Return a PhysicalSize copy from the provided Qt ``size``."""
        qsize = QSizeF(size)
        return cls(qsize.width(), qsize.height())

    def to_qt(self) -> QSizeF:
        """Represent this physical size as a QSizeF."""
        return QSizeF(self.width, self.height)

    def to_logical(self, dpr: float) -> LogicalSize:
        """Convert this physical size to logical units via ``dpr``."""
        divisor = dpr if dpr > 0 else 1.0
        return LogicalSize(self.width / divisor, self.height / divisor)


SupportedValue = Union[
    float,
    int,
    QPointF,
    QPoint,
    QSizeF,
    QSize,
    QRectF,
    LogicalPoint,
    PhysicalPoint,
    LogicalSize,
    PhysicalSize,
]


class CoordinateContext:
    """Expose the qpane's coordinate state with consistent unit handling."""

    _ZOOM_EPSILON = 1e-6
    _MIN_DPR = 1e-6

    def __init__(
        self,
        qpane: "QPane",
        pan_override: Optional[QPointF] = None,
        content_snapshot: "SceneContentSnapshot | None" = None,
    ):
        """Snapshot DPR, image geometry, and viewport pan/zoom for conversions."""
        raw_dpr = float(qpane.devicePixelRatioF())
        if raw_dpr <= self._MIN_DPR:
            logger.warning(
                "devicePixelRatioF() returned %.6f; falling back to 1.0", raw_dpr
            )
            raw_dpr = 1.0
        self.dpr = raw_dpr
        try:
            view = qpane.view()
        except AttributeError as exc:
            raise AttributeError("View accessed before initialization") from exc
        viewport = view.viewport
        self.zoom = float(viewport.zoom)
        # Store the pan in physical pixels (matching the renderer buffers).
        self.pan_phys = PhysicalPoint.from_qt(
            pan_override if pan_override is not None else viewport.pan
        )
        self.qpane_size_logical = LogicalSize.from_qt(qpane.size())
        self._qpane_center_logical = self.qpane_size_logical.half_point()
        self._image_ready = content_snapshot is not None
        base_image_size = (
            content_snapshot.base_image_size
            if content_snapshot is not None
            else QSize()
        )
        self.image_size = PhysicalSize.from_qt(base_image_size)
        if self._image_ready:
            self._image_center = PhysicalPoint(
                self.image_size.width / 2.0,
                self.image_size.height / 2.0,
            )
        else:
            # Image-dependent helpers must refuse to run until an image is loaded.
            self._image_center = PhysicalPoint(0.0, 0.0)
            logger.debug(
                "CoordinateContext initialised without content geometry; only DPR"
                " helpers are available."
            )

    def _require_image(self) -> None:
        """Ensure content geometry is valid before running image-space math."""
        if not self._image_ready:
            raise ValueError(
                "Coordinate conversions require renderable content geometry."
            )

    def _safe_zoom(self) -> float:
        """Return a zoom value guaranteed to be non-zero."""
        if abs(self.zoom) <= self._ZOOM_EPSILON:
            return 1.0
        return self.zoom

    def _pan_logical(self) -> LogicalPoint:
        """Return the stored physical pan converted into logical pixels."""
        return self.pan_phys.to_logical(self.dpr)

    def logical_to_physical(self, value: SupportedValue) -> SupportedValue:
        """Logical units -> Physical pixels (applies DPR)."""
        if isinstance(value, LogicalPoint):
            return value.to_physical(self.dpr)
        if isinstance(value, LogicalSize):
            return value.to_physical(self.dpr)
        if isinstance(value, (int, float)):
            return value * self.dpr
        if isinstance(value, QPoint) or isinstance(value, QPointF):
            return LogicalPoint.from_qt(value).to_physical(self.dpr).to_qt()
        if isinstance(value, QSize) or isinstance(value, QSizeF):
            return LogicalSize.from_qt(value).to_physical(self.dpr).to_qt()
        if isinstance(value, QRectF):
            top_left = (
                LogicalPoint.from_qt(value.topLeft()).to_physical(self.dpr).to_qt()
            )
            size = LogicalSize.from_qt(value.size()).to_physical(self.dpr).to_qt()
            return QRectF(top_left, size)
        if isinstance(value, PhysicalPoint) or isinstance(value, PhysicalSize):
            return value
        raise TypeError(
            f"Unsupported value type for coordinate conversion: {type(value)!r}"
        )

    def physical_to_logical(self, value: SupportedValue) -> SupportedValue:
        """Physical pixels -> Logical units (removes DPR)."""
        if isinstance(value, PhysicalPoint):
            return value.to_logical(self.dpr)
        if isinstance(value, PhysicalSize):
            return value.to_logical(self.dpr)
        if isinstance(value, (int, float)):
            divisor = self.dpr if self.dpr > 0 else 1.0
            return value / divisor
        if isinstance(value, QPoint) or isinstance(value, QPointF):
            return PhysicalPoint.from_qt(value).to_logical(self.dpr).to_qt()
        if isinstance(value, QSize) or isinstance(value, QSizeF):
            return PhysicalSize.from_qt(value).to_logical(self.dpr).to_qt()
        if isinstance(value, QRectF):
            top_left = (
                PhysicalPoint.from_qt(value.topLeft()).to_logical(self.dpr).to_qt()
            )
            size = PhysicalSize.from_qt(value.size()).to_logical(self.dpr).to_qt()
            return QRectF(top_left, size)
        if isinstance(value, LogicalPoint) or isinstance(value, LogicalSize):
            return value
        raise TypeError(
            f"Unsupported value type for coordinate conversion: {type(value)!r}"
        )

    def panel_to_image(self, panel_pos_log: QPointF) -> QPoint:
        """Logical panel pixels -> Integer image pixels."""
        return self.panel_to_image_hit(panel_pos_log).clamped_point

    def panel_to_image_hit(self, panel_pos_log: QPointF) -> PanelHitTest:
        """Logical panel pixels -> hit-test metadata without clamping."""
        self._require_image()
        panel_point = LogicalPoint.from_qt(panel_pos_log)
        pan_log = self._pan_logical()
        centered_log = panel_point.minus(self._qpane_center_logical)
        offset_log = centered_log.minus(pan_log)
        safe_zoom = self._safe_zoom()
        rel_image_log = offset_log.scaled(1.0 / safe_zoom)
        rel_image_phys = rel_image_log.to_physical(self.dpr)
        image_coords = rel_image_phys.plus(self._image_center)
        max_x = max(0, int(self.image_size.width) - 1)
        max_y = max(0, int(self.image_size.height) - 1)
        clamped_x = int(max(0, min(round(image_coords.x), max_x)))
        clamped_y = int(max(0, min(round(image_coords.y), max_y)))
        inside_x = 0 <= image_coords.x < (max_x + 1)
        inside_y = 0 <= image_coords.y < (max_y + 1)
        raw_point = QPointF(float(image_coords.x), float(image_coords.y))
        panel_qpoint = panel_pos_log.toPoint()
        clamped_point = QPoint(clamped_x, clamped_y)
        return PanelHitTest(
            panel_point=panel_qpoint,
            raw_point=raw_point,
            clamped_point=clamped_point,
            inside_image=bool(inside_x and inside_y),
        )

    def image_to_panel(self, image_point: QPointF) -> QPointF:
        """Image pixels -> Logical panel pixels."""
        self._require_image()
        pan_log = self._pan_logical()
        safe_zoom = self._safe_zoom()
        # Work in physical space first so we stay aligned with device pixels.
        image_coords = PhysicalPoint.from_qt(image_point)
        offset_from_center = image_coords.minus(self._image_center)
        offset_panel_phys = offset_from_center.scaled(safe_zoom)
        offset_log = offset_panel_phys.to_logical(self.dpr)
        result_log = self._qpane_center_logical.plus(pan_log).plus(offset_log)
        return result_log.to_qt()

    def get_painter_transform(
        self, source_image_size: QSize, pyramid_scale: float
    ) -> QTransform:
        """Source image pixels -> Logical panel pixels for QPainter."""
        self._require_image()
        pan_log = self._pan_logical()
        safe_zoom = self._safe_zoom()
        # Pyramid levels shrink the source, so adjust the zoom accordingly.
        effective_scale = safe_zoom / pyramid_scale if pyramid_scale != 0 else safe_zoom
        # Convert the scale into logical units so the painter operates in device-independent pixels.
        scale_factor = effective_scale / self.dpr if self.dpr > 0 else effective_scale
        transform = QTransform()
        transform.translate(
            self.qpane_size_logical.width / 2.0,
            self.qpane_size_logical.height / 2.0,
        )
        transform.translate(pan_log.x, pan_log.y)
        transform.scale(scale_factor, scale_factor)
        transform.translate(
            -source_image_size.width() / 2.0,
            -source_image_size.height() / 2.0,
        )
        return transform
