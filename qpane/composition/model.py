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

"""Internal composition records used by the composition service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

import uuid

from PySide6.QtCore import QRectF

from ..types import ComparisonOrientation, QPaneSceneClip


class CompositionKind(str, Enum):
    """Kinds of compositions managed by QPane."""

    DEFAULT_IMAGE = "default-image"
    EXPLICIT = "explicit"
    LAYERED_SCENE = "layered-scene"


@dataclass(frozen=True, slots=True)
class CompositionComparison:
    """Comparison settings owned by one composition."""

    source_id: uuid.UUID
    source_path: Path | None
    source_kind: str
    split_position: float
    orientation: ComparisonOrientation

    def with_split(
        self,
        position: float,
        orientation: ComparisonOrientation,
    ) -> "CompositionComparison":
        """Return a comparison record with updated split settings."""
        return replace(
            self,
            split_position=position,
            orientation=orientation,
        )


@dataclass(frozen=True, slots=True)
class CompositionSceneLayer:
    """Normalized catalog-backed layer stored inside a layered composition."""

    layer_id: uuid.UUID
    image_id: uuid.UUID
    placement: QRectF
    visible: bool
    opacity: float
    clip: QPaneSceneClip | None
    hit_test: bool
    role: str
    metadata: dict[str, object]

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry from stored composition state."""
        object.__setattr__(self, "placement", QRectF(self.placement))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class CompositionScene:
    """Normalized scene payload stored by a layered composition record."""

    bounds: QRectF
    layers: tuple[CompositionSceneLayer, ...]

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry and normalize layer storage."""
        object.__setattr__(self, "bounds", QRectF(self.bounds))
        object.__setattr__(self, "layers", tuple(self.layers))


@dataclass(frozen=True, slots=True)
class CompositionRecord:
    """Persistent internal composition state."""

    composition_id: uuid.UUID
    kind: CompositionKind
    title: str
    source_image_ids: tuple[uuid.UUID, ...]
    primary_image_id: uuid.UUID | None
    comparison: CompositionComparison | None = None
    scene: CompositionScene | None = None

    def with_comparison(
        self, comparison: CompositionComparison | None
    ) -> "CompositionRecord":
        """Return a record with a replaced comparison payload."""
        return replace(self, comparison=comparison)
