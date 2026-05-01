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

"""Build internal scenes for configured placeholder images."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize

from .identity import placeholder_layer_id, placeholder_scene_id, placeholder_source_id
from .model import (
    BlendMode,
    LayerDescriptor,
    LayerHitTest,
    LayerKind,
    LayerPlacement,
    SceneDescriptor,
    SceneKind,
)
from .sources import PlaceholderImageSource


def build_placeholder_scene(
    *,
    image_size: QSize,
    source_path: Path | None,
    revision: int = 0,
) -> SceneDescriptor:
    """Return a one-layer scene representing a configured placeholder image."""
    if image_size.isEmpty() or image_size.width() < 0 or image_size.height() < 0:
        raise ValueError("image_size must describe non-empty placeholder content")
    source_id = placeholder_source_id(source_path)
    scene_id = placeholder_scene_id(source_id)
    placement = LayerPlacement(
        x=0.0,
        y=0.0,
        width=float(image_size.width()),
        height=float(image_size.height()),
    )
    source = PlaceholderImageSource(source_id=source_id, revision=revision)
    layer = LayerDescriptor(
        scene_id=scene_id,
        layer_id=placeholder_layer_id(source_id),
        kind=LayerKind.IMAGE,
        source=source,
        placement=placement,
        visible=True,
        opacity=1.0,
        blend_mode=BlendMode.NORMAL,
        clip=None,
        hit_test=LayerHitTest(enabled=True, selectable=False, role="placeholder-image"),
        source_revision=revision,
    )
    return SceneDescriptor(
        scene_id=scene_id,
        kind=SceneKind.PLACEHOLDER_IMAGE,
        bounds=placement,
        layers=(layer,),
    )
