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

"""Compiled render-facing scene metadata owned by the rendering layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

from ..scene.identity import SceneLayerAssetKey
from ..scene.model import LayerDescriptor, SceneDescriptor
from ..scene.render_plan import SceneContentSnapshot, SceneHitTestItem


@dataclass(frozen=True, slots=True)
class CompiledRenderScene:
    """Static render-facing data compiled from a resolved scene graph."""

    scene: SceneDescriptor
    content_snapshot: SceneContentSnapshot
    layers: tuple["CompiledRenderLayer", ...]
    mask_layers: tuple[LayerDescriptor, ...]
    hit_test_items: tuple[SceneHitTestItem, ...]


@dataclass(frozen=True, slots=True)
class CompiledRenderLayer:
    """Static render metadata for one image layer."""

    descriptor: LayerDescriptor
    asset_key: SceneLayerAssetKey
    pyramid_asset_key: SceneLayerAssetKey
    full_image: QImage
    source_path: Path | None
    source_revision: int
    is_base_raster: bool
    uses_default_base_tile_math: bool

    def __post_init__(self) -> None:
        """Detach mutable Qt image data from caller-owned render inputs."""
        object.__setattr__(self, "full_image", QImage(self.full_image))


def hit_test_items_for_scene(
    scene: SceneDescriptor,
) -> tuple[SceneHitTestItem, ...]:
    """Project scene layer hit-test metadata into render-plan metadata."""
    return tuple(
        SceneHitTestItem(
            scene_id=scene.scene_id,
            layer_id=layer.layer_id,
            bounds=layer.placement,
            enabled=layer.hit_test.enabled,
            selectable=layer.hit_test.selectable,
            role=layer.hit_test.role,
            source=layer.source,
        )
        for layer in scene.layers
        if layer.hit_test.enabled
    )
