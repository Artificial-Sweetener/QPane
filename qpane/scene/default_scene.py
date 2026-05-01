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

"""Build the behavior-preserving default scene for catalog images."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize

from .identity import base_image_layer_id, default_scene_id
from .model import (
    BlendMode,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
    SceneKind,
)
from .providers import SceneContribution
from .sources import CatalogImageSource


def build_default_catalog_scene(
    *,
    image_id: uuid.UUID,
    image_size: QSize,
    source_path: Path | None,
    revision: int,
) -> SceneDescriptor:
    """Return a one-layer scene representing the active catalog image."""
    if image_size.isEmpty() or image_size.width() < 0 or image_size.height() < 0:
        raise ValueError("image_size must describe non-empty catalog content")
    scene_id = default_scene_id(image_id)
    placement = LayerPlacement(
        x=0.0,
        y=0.0,
        width=float(image_size.width()),
        height=float(image_size.height()),
    )
    source = CatalogImageSource(
        image_id=image_id,
        source_path=source_path,
        revision=revision,
    )
    layer = LayerDescriptor(
        scene_id=scene_id,
        layer_id=base_image_layer_id(image_id),
        kind=LayerKind.IMAGE,
        source=source,
        placement=placement,
        visible=True,
        opacity=1.0,
        blend_mode=BlendMode.NORMAL,
        clip=None,
        hit_test=LayerHitTest(enabled=True, selectable=False, role="base-image"),
        source_revision=revision,
    )
    return SceneDescriptor(
        scene_id=scene_id,
        kind=SceneKind.DEFAULT_CATALOG_IMAGE,
        bounds=placement,
        layers=(layer,),
    )


@dataclass(frozen=True, slots=True)
class DefaultCatalogSceneProvider:
    """Provide the default one-layer scene contribution for a catalog image."""

    image_id: uuid.UUID
    image_size: QSize
    source_path: Path | None
    revision: int

    def scene_contribution(self) -> SceneContribution | None:
        """Return the provider contribution for the configured catalog image."""
        scene = build_default_catalog_scene(
            image_id=self.image_id,
            image_size=self.image_size,
            source_path=self.source_path,
            revision=self.revision,
        )
        return SceneContribution(scene=scene, order=0)
