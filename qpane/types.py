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

"""Public type primitives and enums exposed through the qpane facade."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QImage, QTransform

if TYPE_CHECKING:
    from .masks.workflow import MaskInfo
__all__ = [
    "CacheMode",
    "PlaceholderScaleMode",
    "ZoomMode",
    "DiagnosticsDomain",
    "ControlMode",
    "ComparisonOrientation",
    "CatalogEntry",
    "LinkedGroup",
    "ComparisonState",
    "ComparisonDividerState",
    "CompositionEntry",
    "CompositionSnapshot",
    "DiagnosticRecord",
    "OverlayState",
    "MaskInfo",
    "MaskSavedPayload",
    "CatalogSnapshot",
    "QPaneScene",
    "QPaneSceneLayer",
    "QPaneSceneRequest",
    "QPaneCatalogImageLayerRequest",
    "QPaneSceneTemplate",
    "QPaneTemplateLayer",
    "QPaneSceneTemplateBindings",
    "QPaneSceneClip",
    "QPaneSceneHit",
    "QPaneSceneOverlayState",
    "QPaneSceneOverlayLayer",
]


class CacheMode(str, Enum):
    """Cache budgeting strategy."""

    AUTO = "auto"
    HARD = "hard"


class PlaceholderScaleMode(str, Enum):
    """Scaling rule applied to placeholder assets."""

    AUTO = "auto"
    LOGICAL_FIT = "logical_fit"
    PHYSICAL_FIT = "physical_fit"
    RELATIVE_FIT = "relative_fit"


class ZoomMode(str, Enum):
    """Zoom policy used by placeholder rendering."""

    FIT = "fit"
    LOCKED_ZOOM = "locked_zoom"
    LOCKED_SIZE = "locked_size"


class DiagnosticsDomain(str, Enum):
    """Diagnostics categories exposed through the facade."""

    CACHE = "cache"
    SWAP = "swap"
    MASK = "mask"
    EXECUTOR = "executor"
    RETRY = "retry"
    SAM = "sam"


class ControlMode(str, Enum):
    """Built-in control modes supported by the tool manager."""

    CURSOR = "cursor"
    PANZOOM = "panzoom"
    DRAW_BRUSH = "draw-brush"
    SMART_SELECT = "smart-select"


class ComparisonOrientation(str, Enum):
    """Comparison split orientations supported by the facade."""

    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Structured catalog entry containing image data and an optional path."""

    image: QImage
    path: Path | None


@dataclass(frozen=True, slots=True)
class LinkedGroup:
    """Linked-view group descriptor with a stable identifier."""

    group_id: uuid.UUID
    members: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class ComparisonState:
    """Public snapshot of the active comparison setup."""

    enabled: bool
    source_id: uuid.UUID | None
    source_path: Path | None
    source_kind: str | None
    split_position: float
    orientation: ComparisonOrientation


@dataclass(frozen=True, slots=True)
class CompositionEntry:
    """Public snapshot entry for one composition."""

    composition_id: uuid.UUID
    kind: str
    title: str
    source_image_ids: tuple[uuid.UUID, ...]
    current_image_id: uuid.UUID | None
    comparison: ComparisonState
    scene_layer_count: int = 0
    scene_bounds: QRectF | None = None

    def __post_init__(self) -> None:
        """Detach optional Qt geometry from composition snapshots."""
        if self.scene_bounds is not None:
            object.__setattr__(self, "scene_bounds", QRectF(self.scene_bounds))


@dataclass(frozen=True, slots=True)
class CompositionSnapshot:
    """Public snapshot of composition browser state."""

    compositions: dict[uuid.UUID, CompositionEntry]
    order: tuple[uuid.UUID, ...]
    current_composition_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ComparisonDividerState:
    """Public snapshot of comparison divider interaction geometry."""

    enabled: bool = False
    interactive: bool = False
    hovered: bool = False
    dragging: bool = False
    orientation: ComparisonOrientation = ComparisonOrientation.VERTICAL
    hit_width: float = 0.0
    full_segment: QLineF | None = None
    visible_segment: QLineF | None = None

    def __post_init__(self) -> None:
        """Detach mutable Qt line values from internal geometry."""
        if self.full_segment is not None:
            object.__setattr__(self, "full_segment", QLineF(self.full_segment))
        if self.visible_segment is not None:
            object.__setattr__(
                self,
                "visible_segment",
                QLineF(self.visible_segment),
            )


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    """Single name/value diagnostic entry shown in overlays."""

    label: str
    value: str

    def formatted(self) -> str:
        """Return a human-friendly string for display."""
        if not self.label:
            return self.value
        return f"{self.label}: {self.value}"

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        """Return the formatted representation for inline rendering."""
        return self.formatted()


MaskSavedPayload = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Structured catalog state returned by the facade snapshot helper."""

    catalog: dict[uuid.UUID, CatalogEntry]
    linked_groups: tuple[LinkedGroup, ...]
    order: tuple[uuid.UUID, ...]
    current_image_id: uuid.UUID | None
    active_mask_id: uuid.UUID | None
    mask_capable: bool


@dataclass(frozen=True, slots=True)
class OverlayState:
    """Stable overlay context describing the current view and render snapshot."""

    zoom: float
    qpane_rect: QRect
    source_image: QImage
    transform: QTransform
    current_pan: QPointF
    physical_viewport_rect: QRectF


@dataclass(frozen=True, slots=True)
class QPaneSceneClip:
    """Public clip rectangle applied to a composed scene layer."""

    coordinate_space: str
    rect: QRectF

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry from caller-owned clip state."""
        object.__setattr__(self, "rect", QRectF(self.rect))


@dataclass(frozen=True, slots=True)
class QPaneCatalogImageLayerRequest:
    """Catalog-backed image layer requested for a stored scene composition."""

    layer_id: uuid.UUID
    image_id: uuid.UUID
    placement: QRectF
    visible: bool = True
    opacity: float = 1.0
    clip: QPaneSceneClip | None = None
    hit_test: bool = True
    role: str = "content"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Detach mutable geometry and protect request metadata."""
        object.__setattr__(self, "placement", QRectF(self.placement))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class QPaneSceneRequest:
    """Host request for a stored catalog-backed scene composition."""

    composition_id: uuid.UUID | None
    title: str | None
    bounds: QRectF
    layers: tuple[QPaneCatalogImageLayerRequest, ...]

    def __post_init__(self) -> None:
        """Detach mutable geometry and normalize layer storage."""
        object.__setattr__(self, "bounds", QRectF(self.bounds))
        object.__setattr__(self, "layers", tuple(self.layers))


@dataclass(frozen=True, slots=True)
class QPaneTemplateLayer:
    """Reusable template layer that binds to a catalog image source slot."""

    layer_id: uuid.UUID
    source_slot: str
    placement: QRectF
    visible: bool = True
    opacity: float = 1.0
    clip: QPaneSceneClip | None = None
    hit_test: bool = True
    role: str = "content"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Detach mutable geometry and protect template metadata."""
        object.__setattr__(self, "placement", QRectF(self.placement))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class QPaneSceneTemplate:
    """Reusable host-owned template for scene composition requests."""

    template_id: uuid.UUID
    bounds: QRectF
    layers: tuple[QPaneTemplateLayer, ...]
    title: str | None = None

    def __post_init__(self) -> None:
        """Detach mutable geometry and normalize layer storage."""
        object.__setattr__(self, "bounds", QRectF(self.bounds))
        object.__setattr__(self, "layers", tuple(self.layers))


@dataclass(frozen=True, slots=True)
class QPaneSceneTemplateBindings:
    """Concrete catalog bindings used to compose a scene template."""

    composition_id: uuid.UUID | None
    title: str | None = None
    catalog_images: Mapping[str, uuid.UUID] = field(default_factory=dict)
    metadata: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Protect binding mappings from host-side mutation."""
        object.__setattr__(
            self, "catalog_images", MappingProxyType(dict(self.catalog_images))
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(
                {
                    slot: MappingProxyType(dict(values))
                    for slot, values in self.metadata.items()
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class QPaneSceneLayer:
    """Catalog-backed image layer in a public composed scene."""

    layer_id: uuid.UUID
    image_id: uuid.UUID
    placement: QRectF
    visible: bool = True
    opacity: float = 1.0
    clip: QPaneSceneClip | None = None
    hit_test: bool = True
    role: str = "content"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable public layer inputs into QPane-owned values."""
        object.__setattr__(self, "placement", QRectF(self.placement))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class QPaneScene:
    """Normalized public snapshot for an active stored scene composition."""

    composition_id: uuid.UUID
    scene_id: uuid.UUID
    title: str
    bounds: QRectF
    layers: tuple[QPaneSceneLayer, ...]

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry and normalize layer storage."""
        object.__setattr__(self, "bounds", QRectF(self.bounds))
        object.__setattr__(self, "layers", tuple(self.layers))


@dataclass(frozen=True, slots=True)
class QPaneSceneHit:
    """Public hit-test result for a catalog-backed scene layer."""

    composition_id: uuid.UUID
    scene_id: uuid.UUID
    layer_id: uuid.UUID
    image_id: uuid.UUID
    role: str
    metadata: Mapping[str, object]
    panel_point: QPointF
    scene_point: QPointF
    source_point: QPointF

    def __post_init__(self) -> None:
        """Detach mutable Qt values and protect metadata from mutation."""
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "panel_point", QPointF(self.panel_point))
        object.__setattr__(self, "scene_point", QPointF(self.scene_point))
        object.__setattr__(self, "source_point", QPointF(self.source_point))


@dataclass(frozen=True, slots=True)
class QPaneSceneOverlayLayer:
    """Public scene-overlay geometry for one rendered catalog-backed layer."""

    layer_id: uuid.UUID
    image_id: uuid.UUID
    role: str
    metadata: Mapping[str, object]
    placement: QRectF
    source_size: QSize
    transform: QTransform
    panel_bounds: QRectF
    visible: bool

    def __post_init__(self) -> None:
        """Detach mutable overlay geometry and protect metadata from mutation."""
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "placement", QRectF(self.placement))
        object.__setattr__(self, "source_size", QSize(self.source_size))
        object.__setattr__(self, "transform", QTransform(self.transform))
        object.__setattr__(self, "panel_bounds", QRectF(self.panel_bounds))


@dataclass(frozen=True, slots=True)
class QPaneSceneOverlayState:
    """Stable overlay context for host chrome drawn relative to scene layers."""

    zoom: float
    qpane_rect: QRect
    physical_viewport_rect: QRectF
    composition_id: uuid.UUID
    scene_id: uuid.UUID
    scene_bounds: QRectF
    layers: tuple[QPaneSceneOverlayLayer, ...]

    def __post_init__(self) -> None:
        """Detach mutable Qt values from internal render-plan state."""
        object.__setattr__(self, "qpane_rect", QRect(self.qpane_rect))
        object.__setattr__(
            self,
            "physical_viewport_rect",
            QRectF(self.physical_viewport_rect),
        )
        object.__setattr__(self, "scene_bounds", QRectF(self.scene_bounds))
        object.__setattr__(self, "layers", tuple(self.layers))


def __getattr__(name: str) -> Any:
    """Lazily resolve MaskInfo and CatalogMutationEvent to avoid import cycles."""
    if name == "MaskInfo":
        from .masks.workflow import MaskInfo as _MaskInfo

        globals()["MaskInfo"] = _MaskInfo
        return _MaskInfo
    if name == "CatalogMutationEvent":
        from .catalog.catalog import CatalogMutationEvent as _CatalogMutationEvent

        globals()["CatalogMutationEvent"] = _CatalogMutationEvent
        return _CatalogMutationEvent
    raise AttributeError(f"module {__name__!s} has no attribute {name}")
