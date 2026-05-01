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

import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, TypeVar

from PySide6.QtCore import (
    QEvent,
    QLineF,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QEnterEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .catalog import ImageMap
from .concurrency import TaskExecutorProtocol, ThreadPolicy
from .core import (
    CursorProvider,
    OverlayDrawFn,
    SceneOverlayDrawFn,
    ToolFactory,
    ToolSignalBinder,
)
from .masks.workflow import MaskInfo
from .masks.mask_undo import MaskUndoState
from .types import CatalogSnapshot, LinkedGroup

_ConfigT = TypeVar("_ConfigT", bound="Config")

class CacheMode(str, Enum):
    AUTO = "auto"
    HARD = "hard"

class PlaceholderScaleMode(str, Enum):
    AUTO = "auto"
    LOGICAL_FIT = "logical_fit"
    PHYSICAL_FIT = "physical_fit"
    RELATIVE_FIT = "relative_fit"

class ZoomMode(str, Enum):
    FIT = "fit"
    LOCKED_ZOOM = "locked_zoom"
    LOCKED_SIZE = "locked_size"

class ControlMode(str, Enum):
    CURSOR = "cursor"
    PANZOOM = "panzoom"
    DRAW_BRUSH = "draw-brush"
    SMART_SELECT = "smart-select"

class ComparisonOrientation(str, Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"

class DiagnosticsDomain(str, Enum):
    CACHE: str
    SWAP: str
    MASK: str
    EXECUTOR: str
    RETRY: str
    SAM: str

class CatalogEntry:
    image: QImage
    path: Path | None

class OverlayState:
    zoom: float
    qpane_rect: QRect
    source_image: QImage
    transform: QTransform
    current_pan: QPointF
    physical_viewport_rect: QRectF

class QPaneSceneClip:
    coordinate_space: str
    rect: QRectF

class QPaneSceneLayer:
    layer_id: uuid.UUID
    image_id: uuid.UUID
    placement: QRectF
    visible: bool
    opacity: float
    clip: QPaneSceneClip | None
    hit_test: bool
    role: str
    metadata: Mapping[str, object]

class QPaneCatalogImageLayerRequest:
    layer_id: uuid.UUID
    image_id: uuid.UUID
    placement: QRectF
    visible: bool
    opacity: float
    clip: QPaneSceneClip | None
    hit_test: bool
    role: str
    metadata: Mapping[str, object]

class QPaneSceneRequest:
    composition_id: uuid.UUID | None
    title: str | None
    bounds: QRectF
    layers: tuple[QPaneCatalogImageLayerRequest, ...]

class QPaneTemplateLayer:
    layer_id: uuid.UUID
    source_slot: str
    placement: QRectF
    visible: bool
    opacity: float
    clip: QPaneSceneClip | None
    hit_test: bool
    role: str
    metadata: Mapping[str, object]

class QPaneSceneTemplate:
    template_id: uuid.UUID
    bounds: QRectF
    layers: tuple[QPaneTemplateLayer, ...]
    title: str | None

class QPaneSceneTemplateBindings:
    composition_id: uuid.UUID | None
    title: str | None
    catalog_images: Mapping[str, uuid.UUID]
    metadata: Mapping[str, Mapping[str, object]]

class QPaneScene:
    composition_id: uuid.UUID
    scene_id: uuid.UUID
    title: str
    bounds: QRectF
    layers: tuple[QPaneSceneLayer, ...]

class QPaneSceneHit:
    composition_id: uuid.UUID
    scene_id: uuid.UUID
    layer_id: uuid.UUID
    image_id: uuid.UUID
    role: str
    metadata: Mapping[str, object]
    panel_point: QPointF
    scene_point: QPointF
    source_point: QPointF

class QPaneSceneOverlayLayer:
    layer_id: uuid.UUID
    image_id: uuid.UUID
    role: str
    metadata: Mapping[str, object]
    placement: QRectF
    source_size: QSize
    transform: QTransform
    panel_bounds: QRectF
    visible: bool

class QPaneSceneOverlayState:
    zoom: float
    qpane_rect: QRect
    physical_viewport_rect: QRectF
    composition_id: uuid.UUID
    scene_id: uuid.UUID
    scene_bounds: QRectF
    layers: tuple[QPaneSceneOverlayLayer, ...]

class PanelHitTest:
    panel_point: QPoint
    raw_point: QPointF
    clamped_point: QPoint
    inside_image: bool

class ComparisonState:
    enabled: bool
    source_id: uuid.UUID | None
    source_path: Path | None
    source_kind: str | None
    split_position: float
    orientation: ComparisonOrientation

class ComparisonDividerState:
    enabled: bool
    interactive: bool
    hovered: bool
    dragging: bool
    orientation: ComparisonOrientation
    hit_width: float
    full_segment: QLineF | None
    visible_segment: QLineF | None

class CompositionEntry:
    composition_id: uuid.UUID
    kind: str
    title: str
    source_image_ids: tuple[uuid.UUID, ...]
    current_image_id: uuid.UUID | None
    comparison: ComparisonState
    scene_layer_count: int
    scene_bounds: QRectF | None

class CompositionSnapshot:
    compositions: dict[uuid.UUID, CompositionEntry]
    order: tuple[uuid.UUID, ...]
    current_composition_id: uuid.UUID | None

class Config:
    def __init__(self, **overrides: Any) -> None: ...
    @staticmethod
    def feature_descriptors() -> Mapping[str, object]: ...
    def configure(
        self: _ConfigT, config_obj: object | None = ..., **kwargs: Any
    ) -> _ConfigT: ...
    def copy(self: _ConfigT) -> _ConfigT: ...
    def as_dict(self) -> dict[str, Any]: ...

class ExtensionToolSignals(QObject):
    pan_requested: Signal
    zoom_requested: Signal
    repaint_overlay_requested: Signal
    cursor_update_requested: Signal

class ExtensionTool:
    signals: ExtensionToolSignals
    def __init__(self) -> None: ...
    def activate(self, dependencies: Mapping[str, Any]) -> None: ...
    def deactivate(self) -> None: ...
    def mousePressEvent(self, event: QMouseEvent) -> None: ...
    def mouseMoveEvent(self, event: QMouseEvent) -> None: ...
    def mouseReleaseEvent(self, event: QMouseEvent) -> None: ...
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None: ...
    def wheelEvent(self, event: QWheelEvent) -> None: ...
    def enterEvent(self, event: QEnterEvent) -> None: ...
    def leaveEvent(self, event: QEvent) -> None: ...
    def keyPressEvent(self, event: QKeyEvent) -> None: ...
    def keyReleaseEvent(self, event: QKeyEvent) -> None: ...
    def draw_overlay(self, painter: QPainter) -> None: ...
    def getCursor(self) -> QCursor | None: ...

class QPane(QWidget):
    CONTROL_MODE_PANZOOM: str
    CONTROL_MODE_CURSOR: str
    CONTROL_MODE_DRAW_BRUSH: str
    CONTROL_MODE_SMART_SELECT: str

    imageLoaded: Signal
    zoomChanged: Signal
    viewportRectChanged: Signal
    maskSaved: Signal
    maskUndoStackChanged: Signal
    currentImageChanged: Signal
    catalogChanged: Signal
    catalogSelectionChanged: Signal
    linkGroupsChanged: Signal
    diagnosticsOverlayToggled: Signal
    diagnosticsDomainToggled: Signal
    comparisonChanged: Signal
    compositionChanged: Signal
    compositionSelectionChanged: Signal
    sceneChanged: Signal
    samCheckpointStatusChanged: Signal
    samCheckpointProgress: Signal

    def __init__(
        self,
        *,
        config: Config | None = ...,
        features: Iterable[str] | None = ...,
        task_executor: TaskExecutorProtocol | None = ...,
        thread_policy: ThreadPolicy | Mapping[str, Any] | None = ...,
        config_strict: bool = ...,
        **kwargs: Any,
    ) -> None: ...
    @staticmethod
    def imageMapFromLists(
        images: Iterable[QImage],
        paths: Iterable[Path | None] | None = ...,
        ids: Iterable[uuid.UUID] | None = ...,
    ) -> ImageMap: ...
    @staticmethod
    def fitSceneRect(source_size: QSize, target_rect: QRectF) -> QRectF: ...
    @staticmethod
    def fillSceneRect(source_size: QSize, target_rect: QRectF) -> QRectF: ...
    @property
    def settings(self) -> Config: ...
    @settings.setter
    def settings(self, new_settings: Config) -> None: ...
    @property
    def installedFeatures(self) -> tuple[str, ...]: ...
    def placeholderActive(self) -> bool: ...
    @property
    def currentImage(self) -> QImage | None: ...
    @property
    def currentImagePath(self) -> Path | None: ...
    @property
    def allImages(self) -> list[QImage]: ...
    @property
    def allImagePaths(self) -> list[Path | None]: ...
    def imagePath(self, image_id: uuid.UUID | None) -> Path | None: ...
    def currentImageID(self) -> uuid.UUID | None: ...
    def imageIDs(self) -> list[uuid.UUID]: ...
    def hasImages(self) -> bool: ...
    def linkedGroups(self) -> tuple[LinkedGroup, ...]: ...
    def currentCompositionID(self) -> uuid.UUID | None: ...
    def compositionIDs(self) -> list[uuid.UUID]: ...
    def getCompositionSnapshot(self) -> CompositionSnapshot: ...
    def activeMaskID(self) -> uuid.UUID | None: ...
    def maskIDsForImage(self, image_id: uuid.UUID | None = ...) -> list[uuid.UUID]: ...
    def listMasksForImage(
        self, image_id: uuid.UUID | None = ...
    ) -> tuple[MaskInfo, ...]: ...
    def getActiveMaskImage(self) -> QImage | None: ...
    def getMaskUndoState(self, mask_id: uuid.UUID) -> MaskUndoState | None: ...
    def diagnosticsOverlayEnabled(self) -> bool: ...
    def diagnosticsDomains(self) -> tuple[str, ...]: ...
    def diagnosticsDomainEnabled(self, domain: str | DiagnosticsDomain) -> bool: ...
    def maskFeatureAvailable(self) -> bool: ...
    def samFeatureAvailable(self) -> bool: ...
    def samCheckpointReady(self) -> bool: ...
    def samCheckpointPath(self) -> Path | None: ...
    def refreshSamFeature(self) -> tuple[bool, str]: ...
    def availableControlModes(self) -> tuple[str, ...]: ...
    def getControlMode(self) -> str: ...
    def currentZoom(self) -> float: ...
    def currentViewportRect(self) -> QRectF: ...
    def setZoomFit(self) -> None: ...
    def setZoom1To1(self, anchor: QPoint | QPointF | None = ...) -> None: ...
    def applyZoom(
        self,
        requested_zoom: float,
        anchor: QPoint | QPointF | None = ...,
    ) -> None: ...
    def panelHitTest(self, panel_pos: QPoint) -> PanelHitTest | None: ...
    def applySettings(
        self, *, config: Config | None = ..., **overrides: Any
    ) -> None: ...
    def setDiagnosticsOverlayEnabled(self, enabled: bool) -> None: ...
    def setDiagnosticsDomainEnabled(
        self, domain: str | DiagnosticsDomain, enabled: bool
    ) -> None: ...
    def registerOverlay(
        self,
        name: str,
        draw_fn: OverlayDrawFn,
    ) -> None: ...
    def unregisterOverlay(self, name: str) -> None: ...
    def contentOverlays(self) -> Mapping[str, OverlayDrawFn]: ...
    def composeScene(
        self,
        request: QPaneSceneRequest,
        *,
        activate: bool = ...,
        fit_view: bool = ...,
    ) -> uuid.UUID: ...
    def composeSceneFromTemplate(
        self,
        template: QPaneSceneTemplate,
        bindings: QPaneSceneTemplateBindings,
        *,
        activate: bool = ...,
        fit_view: bool = ...,
    ) -> uuid.UUID: ...
    def currentScene(self) -> QPaneScene | None: ...
    def sceneHitTest(self, panel_pos: QPoint) -> QPaneSceneHit | None: ...
    def registerSceneOverlay(
        self,
        name: str,
        draw_fn: SceneOverlayDrawFn,
    ) -> None: ...
    def unregisterSceneOverlay(self, name: str) -> None: ...
    def sceneOverlays(self) -> Mapping[str, SceneOverlayDrawFn]: ...
    def overlaysSuspended(self) -> bool: ...
    def overlaysResumePending(self) -> bool: ...
    def resumeOverlays(self) -> None: ...
    def resumeOverlaysAndUpdate(self) -> None: ...
    def maybeResumeOverlays(self) -> None: ...
    def registerCursorProvider(self, mode: str, provider: CursorProvider) -> None: ...
    def unregisterCursorProvider(self, mode: str) -> None: ...
    def registerTool(
        self,
        mode: str,
        factory: ToolFactory,
        *,
        on_connect: ToolSignalBinder | None = ...,
        on_disconnect: ToolSignalBinder | None = ...,
    ) -> None: ...
    def unregisterTool(self, mode: str) -> None: ...
    def setImagesByID(
        self,
        image_map: ImageMap,
        current_id: uuid.UUID,
    ) -> None: ...
    def clearImages(self) -> None: ...
    def removeImageByID(self, image_id: uuid.UUID) -> None: ...
    def removeImagesByID(self, image_ids: list[uuid.UUID]) -> None: ...
    def setCurrentImageID(self, image_id: uuid.UUID | None) -> None: ...
    def setAllImagesLinked(self, enabled: bool) -> None: ...
    def setLinkedGroups(self, groups: Iterable[LinkedGroup]) -> None: ...
    def compose(
        self,
        *,
        images: Iterable[uuid.UUID],
        title: str | None = ...,
    ) -> uuid.UUID: ...
    def openComposition(self, composition_id: uuid.UUID) -> None: ...
    def removeComposition(self, composition_id: uuid.UUID) -> None: ...
    def getCatalogSnapshot(self) -> CatalogSnapshot: ...
    def createBlankMask(self, size: QSize) -> uuid.UUID | None: ...
    def loadMaskFromFile(self, path: str) -> uuid.UUID | None: ...
    def removeMaskFromImage(self, image_id: uuid.UUID, mask_id: uuid.UUID) -> bool: ...
    def setActiveMaskID(self, mask_id: uuid.UUID | None) -> bool: ...
    def setMaskProperties(
        self,
        mask_id: uuid.UUID,
        color: QColor | None = ...,
        opacity: float | None = ...,
    ) -> bool: ...
    def prefetchMaskOverlays(
        self, image_id: uuid.UUID | None, *, reason: str = ...
    ) -> bool: ...
    def cycleMasksForward(self) -> bool: ...
    def cycleMasksBackward(self) -> bool: ...
    def undoMaskEdit(self) -> bool: ...
    def redoMaskEdit(self) -> bool: ...
    def setControlMode(
        self,
        mode: str,
    ) -> None: ...
    def setComparisonImageID(self, image_id: uuid.UUID) -> None: ...
    def clearComparisonImage(self) -> None: ...
    def setComparisonSplit(
        self,
        position: float,
        orientation: ComparisonOrientation | str | None = ...,
    ) -> None: ...
    def comparisonState(self) -> ComparisonState: ...
    def comparisonDividerInteractive(self) -> bool: ...
    def setComparisonDividerInteractive(self, enabled: bool) -> None: ...
    def comparisonDividerState(self) -> ComparisonDividerState: ...
