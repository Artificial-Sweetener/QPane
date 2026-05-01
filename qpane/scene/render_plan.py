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

"""Immutable render-plan snapshots resolved from internal scene descriptors."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeAlias

from PySide6.QtCore import QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QImage, QPixmap, QTransform

from .identity import SceneLayerAssetKey
from .model import LayerClip, LayerDescriptor, LayerPlacement
from .sources import LayerSource


class RenderStrategy(str, Enum):
    """Supported raster rendering strategies."""

    DIRECT = "direct"
    TILE = "tile"


@dataclass(frozen=True, slots=True)
class TileRenderData:
    """Rendered tile payload and source-space draw position."""

    image: QImage
    draw_pos: QPointF

    def __post_init__(self) -> None:
        """Detach mutable Qt values from the caller-owned render inputs."""
        object.__setattr__(self, "draw_pos", QPointF(self.draw_pos))


@dataclass(frozen=True, slots=True)
class SceneContentSnapshot:
    """Content geometry and identity for the active rendered scene."""

    scene_id: uuid.UUID
    base_asset_key: SceneLayerAssetKey
    base_image_size: QSize
    scene_bounds: LayerPlacement
    active_content_bounds: LayerPlacement
    current_path: Path | None

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry values from caller-owned content state."""
        object.__setattr__(self, "base_image_size", QSize(self.base_image_size))


@dataclass(frozen=True, slots=True)
class RasterLayerRenderItem:
    """Render-ready raster layer snapshot consumed by the painting pipeline."""

    descriptor: LayerDescriptor
    source_image: QImage
    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey
    pyramid_scale: float
    transform: QTransform
    placement: LayerPlacement
    clip: LayerClip | None
    strategy: RenderStrategy
    render_hint_enabled: bool
    debug_draw_tile_grid: bool
    tiles_to_draw: tuple[TileRenderData, ...]
    tile_size: int
    tile_overlap: int
    max_tile_cols: int
    max_tile_rows: int
    visible_tile_range: tuple[int, int, int, int] | None

    def __post_init__(self) -> None:
        """Validate stable raster planning values."""
        object.__setattr__(self, "transform", QTransform(self.transform))
        object.__setattr__(self, "tiles_to_draw", tuple(self.tiles_to_draw))
        if self.pyramid_scale <= 0.0:
            raise ValueError("pyramid scale must be positive")
        if self.tile_size < 0 or self.tile_overlap < 0:
            raise ValueError("tile metadata must be non-negative")
        if self.max_tile_cols < 0 or self.max_tile_rows < 0:
            raise ValueError("tile grid dimensions must be non-negative")


@dataclass(frozen=True, slots=True)
class MaskLayerRenderItem:
    """Render-ready mask layer snapshot consumed by scene painting."""

    descriptor: LayerDescriptor
    pixmap: QPixmap
    asset_key: SceneLayerAssetKey
    transform: QTransform
    placement: LayerPlacement
    clip: LayerClip | None
    render_hint_enabled: bool
    scale: float | None

    def __post_init__(self) -> None:
        """Detach mutable Qt values from caller-owned render inputs."""
        object.__setattr__(self, "pixmap", QPixmap(self.pixmap))
        object.__setattr__(self, "transform", QTransform(self.transform))


SceneRenderItem: TypeAlias = RasterLayerRenderItem | MaskLayerRenderItem


@dataclass(frozen=True, slots=True)
class SceneHitTestItem:
    """Render-plan hit-test metadata for a resolved scene layer."""

    scene_id: uuid.UUID
    layer_id: uuid.UUID
    bounds: LayerPlacement
    enabled: bool
    selectable: bool
    role: str
    source: LayerSource | None = None


@dataclass(frozen=True, slots=True)
class SceneLayerHitTestResult:
    """Internal hit-test result for a scene layer under a panel coordinate."""

    scene_id: uuid.UUID
    layer_id: uuid.UUID
    role: str
    source: LayerSource
    panel_point: QPointF
    scene_point: QPointF
    source_point: QPointF
    selectable: bool


@dataclass(frozen=True, slots=True)
class SceneRenderPlan:
    """Render-ready snapshot for one resolved scene frame."""

    scene_id: uuid.UUID
    scene_bounds: LayerPlacement
    content_bounds: LayerPlacement
    content_snapshot: SceneContentSnapshot
    zoom: float
    current_pan: QPointF
    qpane_rect: QRect
    physical_viewport_rect: QRectF
    render_items: tuple[SceneRenderItem, ...]
    hit_test_items: tuple[SceneHitTestItem, ...]

    def __post_init__(self) -> None:
        """Detach mutable Qt geometry values from caller-owned frame state."""
        object.__setattr__(self, "current_pan", QPointF(self.current_pan))
        object.__setattr__(self, "qpane_rect", QRect(self.qpane_rect))
        object.__setattr__(
            self,
            "physical_viewport_rect",
            QRectF(self.physical_viewport_rect),
        )
        object.__setattr__(self, "render_items", tuple(self.render_items))
        object.__setattr__(self, "hit_test_items", tuple(self.hit_test_items))

    @property
    def base_raster_item(self) -> RasterLayerRenderItem | None:
        """Return the default base-image raster item when one exists."""
        for item in self.render_items:
            if isinstance(
                item, RasterLayerRenderItem
            ) and item.descriptor.hit_test.role in {"base-image", "placeholder-image"}:
                return item
        return None
