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

"""Immutable scene and layer descriptors for internal composition state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from .sources import LayerSource


class SceneKind(str, Enum):
    """Kinds of scene documents understood by the internal resolver."""

    DEFAULT_CATALOG_IMAGE = "default-catalog-image"
    PLACEHOLDER_IMAGE = "placeholder-image"
    EXPLICIT = "explicit"


class LayerKind(str, Enum):
    """Kinds of content layers that can appear in a scene descriptor."""

    IMAGE = "image"
    MASK = "mask"


class BlendMode(str, Enum):
    """Supported layer blend modes."""

    NORMAL = "normal"


class ClipCoordinateSpace(str, Enum):
    """Coordinate spaces supported by layer clip rectangles."""

    SCENE = "scene"
    NORMALIZED_SCENE = "normalized-scene"
    VIEWPORT = "viewport"
    NORMALIZED_VIEWPORT = "normalized-viewport"


@dataclass(frozen=True, slots=True)
class LayerPlacement:
    """Scene-space rectangle describing where a layer is positioned."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Validate that placement dimensions are usable."""
        if self.width < 0 or self.height < 0:
            raise ValueError("layer placement dimensions must be non-negative")


@dataclass(frozen=True, slots=True)
class LayerClip:
    """Optional rectangle constraining layer visibility in a known space."""

    coordinate_space: ClipCoordinateSpace
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Validate that clip dimensions are usable."""
        if self.width < 0 or self.height < 0:
            raise ValueError("layer clip dimensions must be non-negative")


@dataclass(frozen=True, slots=True)
class LayerHitTest:
    """Metadata controlling whether a layer participates in scene hit testing."""

    enabled: bool = True
    selectable: bool = False
    role: str = "content"


@dataclass(frozen=True, slots=True)
class LayerDescriptor:
    """Immutable description of one layer inside a resolved scene."""

    scene_id: uuid.UUID
    layer_id: uuid.UUID
    kind: LayerKind
    source: LayerSource
    placement: LayerPlacement
    visible: bool = True
    opacity: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL
    clip: LayerClip | None = None
    hit_test: LayerHitTest = LayerHitTest()
    source_revision: int = 0

    def __post_init__(self) -> None:
        """Validate stable descriptor values."""
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("layer opacity must be between 0.0 and 1.0")
        if self.source_revision < 0:
            raise ValueError("layer source revision must be non-negative")


@dataclass(frozen=True, slots=True)
class SceneDescriptor:
    """Immutable composition descriptor returned by the scene resolver."""

    scene_id: uuid.UUID
    kind: SceneKind
    bounds: LayerPlacement
    layers: tuple[LayerDescriptor, ...]

    def __post_init__(self) -> None:
        """Ensure every layer belongs to this scene."""
        for layer in self.layers:
            if layer.scene_id != self.scene_id:
                raise ValueError("scene layers must reference their owning scene")
